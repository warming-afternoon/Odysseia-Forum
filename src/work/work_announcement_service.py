"""作品公告领域服务。"""

import hashlib
import json

from dto.work.work_command_qo import WorkCommandQo
from fastapi import HTTPException
from sqlalchemy import select, func, text
from shared.time_utils import utc_now
from models.work import Work
from models.work_announcement import WorkAnnouncement
from work.work_service import WorkService
from work.announcement_authority import require_announcement_manager
from work.notification_outbox import enqueue_announcement_invalidation
from work.work_notice_fanout import notify_work_followers


def _validate_announcement(body: dict, *, create: bool) -> None:
    """统一发布和编辑校验，不允许类型错误或空正文。"""
    if not body or set(body) - {"body", "title", "images"}:
        raise HTTPException(422, "无效公告字段")
    if create and "body" not in body:
        raise HTTPException(422, "需要公告正文")
    if "body" in body and (
        not isinstance(body["body"], str)
        or not body["body"].strip()
        or len(body["body"]) > 10000
    ):
        raise HTTPException(422, "公告正文长度无效")
    if (
        "title" in body
        and body["title"] is not None
        and (not isinstance(body["title"], str) or len(body["title"]) > 200)
    ):
        raise HTTPException(422, "公告标题无效")
    if "images" in body and (
        not isinstance(body["images"], list)
        or len(body["images"]) > 20
        or any(
            not isinstance(item, str) or not item.strip() or len(item) > 2048
            for item in body["images"]
        )
    ):
        raise HTTPException(422, "公告图片无效")


class WorkAnnouncementService:
    """公告历史与关注者快照共用发布事务。"""

    def __init__(self, sessions):
        """复用由路由注入的会话工厂，跨表操作在服务层协调。"""
        self.sessions = sessions

    async def announcements(
        self, work_id: int, command: WorkCommandQo, offset: int = 0, limit: int = 20
    ):
        """历史公告可读，不为新关注者补发通知。"""
        principal = command.actor
        async with self.sessions() as session:
            await WorkService(session).visible(work_id, principal)
            offset, limit = max(0, offset), min(100, max(1, limit))
            total = (
                await session.execute(
                    select(func.count())
                    .select_from(WorkAnnouncement)
                    .where(WorkAnnouncement.work_id == work_id)
                )
            ).scalar_one()
            rows = (
                (
                    await session.execute(
                        select(WorkAnnouncement)
                        .where(WorkAnnouncement.work_id == work_id)
                        .order_by(
                            WorkAnnouncement.created_at.desc(),
                            WorkAnnouncement.id.desc(),
                        )
                        .offset(offset)
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            return {
                "items": [
                    {**row.model_dump(), "id": str(row.id), "work_id": str(row.work_id)}
                    for row in rows
                ],
                "total": total,
                "offset": offset,
                "limit": limit,
            }

    async def publish_announcement(
        self, work_id: int, command: WorkCommandQo, idempotency_key: str | None = None
    ):
        """每次手动发布在同一事务中快照关注者并写入独立通知。"""
        principal = command.actor
        body = dict(command.values)
        if not principal["can_speak"]:
            raise HTTPException(403, "当前禁止发言")
        _validate_announcement(body, create=True)
        async with self.sessions() as session:
            await session.execute(
                select(Work.id).where(Work.id == work_id).with_for_update()
            )
            await require_announcement_manager(session, work_id, principal)
            row = await self.insert_announcement(
                session,
                work_id,
                principal["id"],
                body,
                idempotency_key,
                actor_discord_id=int(principal["discord_user_id"]),
            )
            result = {
                **row.model_dump(),
                "id": str(row.id),
                "work_id": str(row.work_id),
            }
            await session.commit()
            return result

    @staticmethod
    async def insert_announcement(
        session,
        work_id,
        actor_id,
        body,
        idempotency_key=None,
        *,
        actor_discord_id=None,
    ):
        """在调用方持有作品锁的事务中写入公告、幂等记录和本地通知。"""
        _validate_announcement(body, create=True)
        key_hash = request_hash = None
        if idempotency_key is not None:
            key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
            request_hash = hashlib.sha256(
                json.dumps(
                    body, sort_keys=True, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest()
            lock_key = f"announcement:{actor_id}:{work_id}:{key_hash}"
            await session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": lock_key},
            )
            existing = (
                await session.execute(
                    text("""SELECT announcement_id, request_hash FROM work_announcement_idempotency
                        WHERE actor_id = :actor AND work_id = :work AND key_hash = :key"""),
                    {"actor": actor_id, "work": work_id, "key": key_hash},
                )
            ).first()
            if existing:
                if existing.request_hash != request_hash:
                    raise HTTPException(409, "幂等键已用于不同的公告内容")
                previous = await session.get(WorkAnnouncement, existing.announcement_id)
                if previous is None:
                    raise HTTPException(503, "公告幂等记录无效")
                return previous
        row = WorkAnnouncement(
            work_id=work_id,
            author_id=actor_id,
            title=body.get("title"),
            body=body["body"],
            images=body.get("images", []),
        )
        session.add(row)
        await session.flush()
        if key_hash is not None:
            await session.execute(
                text("""INSERT INTO work_announcement_idempotency
                        (actor_id, work_id, key_hash, request_hash, announcement_id)
                        VALUES (:actor, :work, :key, :request, :announcement)"""),
                {
                    "actor": actor_id,
                    "work": work_id,
                    "key": key_hash,
                    "request": request_hash,
                    "announcement": row.id,
                },
            )
        await notify_work_followers(
            session,
            work_id=work_id,
            event_type="work_announcement",
            source_id=row.id,
            created_at=row.created_at,
            actor_id=actor_id,
            actor_discord_id=actor_discord_id,
        )
        return row

    async def edit_announcement(
        self,
        work_id: int,
        announcement_id: int,
        command: WorkCommandQo,
        expected_revision: int | None = None,
    ):
        """编辑不通知，撤回保留原公告和原通知关联。"""
        principal = command.actor
        async with self.sessions() as session:
            await session.execute(
                select(Work.id).where(Work.id == work_id).with_for_update()
            )
            await require_announcement_manager(session, work_id, principal)
            row = await self.update_announcement(
                session,
                work_id,
                announcement_id,
                principal["id"],
                dict(command.values),
                command.operation,
                expected_revision,
            )
            revision = row.revision
            await session.commit()
        return {
            "updated": True,
            "withdrawn": command.operation == "withdraw",
            "revision": revision,
        }

    @staticmethod
    async def update_announcement(
        session,
        work_id,
        announcement_id,
        actor_id,
        body,
        operation,
        expected_revision=None,
    ):
        """通过公告行锁序列化编辑和撤回，刷新原通知。"""
        if operation != "withdraw":
            _validate_announcement(body, create=False)
        elif body:
            raise HTTPException(422, "撤回不接受公告字段")
        row = (
            await session.execute(
                select(WorkAnnouncement)
                .where(
                    WorkAnnouncement.id == announcement_id,
                    WorkAnnouncement.work_id == work_id,
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise HTTPException(404, "公告不存在")
        if operation == "withdraw" and row.withdrawn_at is not None:
            return row
        if row.withdrawn_at is not None:
            raise HTTPException(409, "公告已撤回")
        if expected_revision is not None and row.revision != expected_revision:
            raise HTTPException(409, "公告已被其他人修改，请刷新后重试")
        if operation == "withdraw":
            row.withdrawn_at = utc_now()
            row.withdrawn_by = actor_id
        else:
            for key, value in body.items():
                setattr(row, key, value)
            row.edited_by = actor_id
            row.edited_at = utc_now()
        row.revision += 1
        await enqueue_announcement_invalidation(session, row)
        return row
