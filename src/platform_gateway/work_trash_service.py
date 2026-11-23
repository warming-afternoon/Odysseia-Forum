"""平台作品的回收站与治理审计。"""

from fastapi import HTTPException
from sqlalchemy import delete, select, text

from models.work import Work
from models.work_statistics import WorkStatistics
from platform_bus.outbox import enqueue_work
from platform_gateway.http import cursor_offset, invalid, page_result, timestamp
from platform_gateway.policy import valid_bigint, visible_conditions
from platform_gateway.profile_client import core_profiles
from platform_gateway.work_api_service import integer_arg
from platform_gateway.work_repository import WorkRepository, public_user
from shared.time_utils import utc_now


async def audit_work(session, work, actor, action, reason):
    """保留治理动作的最小审计字段，不记录可能永久删除的原文。"""
    await session.execute(
        text(
            "INSERT INTO platform_work_audit (work_id,author_id,actor_id,action,reason,created_at) "
            "VALUES (:id,:author,:actor,:action,:reason,:at)"
        ),
        {
            "id": work.id,
            "author": work.author_id,
            "actor": actor,
            "action": action,
            "reason": reason or None,
            "at": utc_now(),
        },
    )


class WorkTrashService:
    """按当前模式及权限上限处理平台原生作品的删除记录。"""

    def __init__(self, session, principal):
        """使用当前账号和事务解析回收站里的作品。"""
        self.session = session
        self.principal = principal

    def _trash_query(self):
        """仅回收本站软删且满足模式过滤的作品。"""
        return select(Work).where(
            Work.source_platform == "platform",
            *visible_conditions(self.principal, trash=True),
        )

    def _restore_block(self, work):
        """本人的管理删除可撤销；其他人需要回收站权限和排序证明。"""
        actor = self.principal.userId
        staff_deleted = work.deleted_user_id != work.author_id
        if staff_deleted and actor == work.deleted_user_id:
            return None
        if not self.principal.has_any("trash.manage"):
            return "forbidden"
        if staff_deleted:
            ceiling = self.principal.adminCeiling
            if (
                not ceiling
                or ceiling.higher is None
                or work.deleted_user_id in ceiling.higher
            ):
                return "rank"
        return None

    async def list(self, query):
        """按删除时间分页，批量解析作者和删除者公开资料。"""
        self.principal.require_user()
        if not self.principal.has_any("trash.manage"):
            raise HTTPException(403, "需要回收站权限")
        offset = cursor_offset(query.get("cursor"))
        limit = min(integer_arg(query.get("limit", "20"), "limit", minimum=1), 100)
        kind = query.get("type", "work")
        if kind in {"discussion", "post"}:
            return page_result([], offset, limit)
        if kind != "work":
            raise invalid("type")
        statement = self._trash_query()
        if query.get("authorId"):
            statement = statement.where(Work.author_id == query["authorId"])
        source = query.get("source")
        if source == "self":
            statement = statement.where(Work.deleted_user_id == Work.author_id)
        elif source == "admin":
            statement = statement.where(Work.deleted_user_id != Work.author_id)
        elif source is not None:
            raise invalid("source")
        statement = (
            statement.order_by(Work.deleted_at.desc(), Work.id.desc())
            .offset(offset)
            .limit(limit + 1)
        )
        rows = (await self.session.scalars(statement)).all()
        profiles = await core_profiles(
            value for work in rows for value in (work.author_id, work.deleted_user_id)
        )
        result = []
        for work in rows:
            blocked = self._restore_block(work)
            result.append(
                {
                    "id": f"work:{work.id}",
                    "module": "works",
                    "type": "work",
                    "targetId": str(work.id),
                    "title": work.title,
                    "body": work.description,
                    "data": None,
                    "rating": work.rating or ("r18" if work.is_nsfw else "general"),
                    "author": (
                        {
                            **public_user(work.author_id, None, work.created_at),
                            **profiles.get(work.author_id, {}),
                        }
                        if work.author_id
                        else None
                    ),
                    "parent": None,
                    "deletedBy": (
                        {
                            **public_user(work.deleted_user_id, None, work.deleted_at),
                            **profiles.get(work.deleted_user_id, {}),
                        }
                        if work.deleted_user_id
                        else None
                    ),
                    "source": "self"
                    if work.deleted_user_id == work.author_id
                    else "admin",
                    "reason": work.deletion_reason
                    if work.deletion_reason != "self"
                    else None,
                    "deletedAt": timestamp(work.deleted_at),
                    "fileCount": 0,
                    "filesPurgeAt": None,
                    "filesPurgedAt": None,
                    "restorable": blocked is None,
                    "blockedReason": blocked,
                }
            )
        return page_result(result, offset, limit)

    async def restore(self, work_id, body, *, admin=False):
        """保留发布状态，清除删除信息并事务内发布恢复事件。"""
        actor = self.principal.require_user()
        if not valid_bigint(work_id):
            raise HTTPException(404, "资源不存在")
        statement = (
            self._trash_query().where(Work.id == int(work_id)).with_for_update(of=Work)
        )
        work = (await self.session.scalars(statement)).one_or_none()
        if work is None:
            if not admin:
                from platform_gateway.work_api_service import WorkApiService

                await WorkApiService(self.session, self.principal).visible(
                    work_id, own_draft=True
                )
                raise HTTPException(409, "作品未删除")
            raise HTTPException(404, "资源不存在")
        if self._restore_block(work):
            raise HTTPException(403, "没有恢复权限")
        now = utc_now()
        work.deleted_at = None
        work.deletion_reason = None
        work.deleted_user_id = None
        work.updated_at = now
        await audit_work(self.session, work, actor, "work.restore", body.reason)
        if work.published_at:
            await enqueue_work(self.session, work, "work.restored")
        result = {"ok": True, "itemId": f"work:{work.id}", "restoredAt": timestamp(now)}
        if not admin:
            result = (
                await WorkRepository(self.session).project([work], self.principal)
            )[0]
        await self.session.commit()
        return result

    async def purge(self, work_id, body):
        """超级管理员永久删除回收站中的作品，并排队清除跨模块投影。"""
        actor = self.principal.require_user()
        if not self.principal.superadmin:
            raise HTTPException(403, "只有超级管理员能立即永久删除")
        if not body.reason:
            raise invalid("reason", "永久删除必须填写原因")
        if not valid_bigint(work_id):
            raise HTTPException(404, "资源不存在")
        statement = (
            self._trash_query().where(Work.id == int(work_id)).with_for_update(of=Work)
        )
        work = (await self.session.scalars(statement)).one_or_none()
        if work is None:
            raise HTTPException(404, "资源不存在")
        if work.author_id != actor and not self.principal.may_moderate(
            work.author_id, {"trash.manage"}
        ):
            raise HTTPException(403, "只能处置排在自己之下的人")
        await audit_work(self.session, work, actor, "work.purge", body.reason)
        if work.published_at:
            await enqueue_work(self.session, work, "work.purged")
        # 永久删除时清理作品内的私人关系、规则提醒与公告，保留独立治理审计。
        await self.session.execute(
            text("""WITH removed AS (
                DELETE FROM notification WHERE work_id=:id AND event_type IN
                    ('work_rules','work_announcement') RETURNING fluxer_user_id
            ) INSERT INTO work_notification_outbox(recipient_id)
              SELECT DISTINCT fluxer_user_id FROM removed WHERE fluxer_user_id IS NOT NULL"""),
            {"id": work.id},
        )
        for table in ("work_ack", "work_like", "thread_follow", "work_announcement_idempotency", "work_announcement"):
            await self.session.execute(text(f"DELETE FROM {table} WHERE work_id=:id"), {"id": work.id})
        await self.session.execute(
            delete(WorkStatistics).where(WorkStatistics.work_id == work.id)
        )
        await self.session.delete(work)
        await self.session.commit()


def trash_work_id(item_id):
    """作品回收站采用协议的 work: 编号。"""
    prefix, _, work_id = item_id.partition(":")
    if prefix != "work" or not valid_bigint(work_id):
        raise HTTPException(404, "资源不存在")
    return work_id
