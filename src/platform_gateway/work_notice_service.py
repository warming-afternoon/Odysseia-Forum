"""平台公告、作品规则和读者确认事务。"""

import base64
import binascii

from fastapi import HTTPException
from sqlalchemy import select, text, update

from models.notification import Notification
from models.user import User
from models.work_announcement import WorkAnnouncement
from platform_gateway.http import invalid, timestamp
from platform_gateway.policy import valid_bigint
from platform_gateway.profile_client import core_profiles
from platform_gateway.work_api_service import WorkApiService
from shared.time_utils import utc_now
from work.notification_outbox import enqueue_notification_change
from work.work_ack_repository import advance_work_ack
from work.work_announcement_service import WorkAnnouncementService
from work.work_notice_fanout import notify_work_followers


class WorkNoticeService:
    """使用平台可见性和本地动态完成公告及规则操作。"""

    def __init__(self, session, principal):
        """复用单请求会话和作品可见性服务。"""
        self.session = session
        self.principal = principal
        self.works = WorkApiService(session, principal)

    async def managed_work(self, work_id, *, publishing):
        """作者或符合处置范围的站务可以维护作品公告和规则。"""
        user_id = self.principal.require_user()
        work = await self.works.visible(work_id, lock=True)
        if work.author_id != user_id and not self.principal.may_moderate(
            work.author_id, {"works.manage_any"}
        ):
            raise HTTPException(403, "需要作者或站务权限")
        if publishing:
            self.principal.require_publication()
        return work

    async def announcement_views(self, work, rows):
        """批量组装当前页公告的公开作者资料，保留历史作者占位。"""
        author_ids = {row.author_id for row in rows if row.author_id}
        local_users = {}
        if author_ids:
            local_rows = await self.session.scalars(
                select(User).where(User.id.in_(author_ids))
            )
            local_users = {user.id: user for user in local_rows}
        profiles = await core_profiles(author_ids)
        views = []
        for row in rows:
            author = local_users.get(row.author_id)
            withdrawn = row.withdrawn_at is not None
            views.append(
                {
                    "id": str(row.id),
                    "workId": str(work.id),
                    "title": None if withdrawn else row.title,
                    "body": "" if withdrawn else row.body,
                    "images": [] if withdrawn else row.images,
                    "withdrawn": withdrawn,
                    "withdrawnAt": timestamp(row.withdrawn_at),
                    "version": row.version,
                    "revision": row.revision,
                    "author": {
                        "id": row.author_id or f"legacy:work:{work.id}",
                        "username": author.username if author else "",
                        "displayName": (author.display_name or author.username)
                        if author
                        else "未知用户",
                        "avatar": None,
                        "decorations": {"badges": []},
                        "createdAt": timestamp(row.created_at),
                        "discordLinked": False,
                        "isNewcomer": False,
                        **profiles.get(row.author_id, {}),
                    },
                    "createdAt": timestamp(row.created_at),
                    "editedAt": timestamp(row.edited_at),
                }
            )
        return views

    async def list_announcements(self, work_id, cursor, limit):
        """按作品内发布序号分页，已撤回公告仅返回状态和元数据。"""
        work = await self.works.visible(work_id)
        query = select(WorkAnnouncement).where(
            WorkAnnouncement.work_id == work.id,
        )
        if cursor is not None:
            try:
                decoded = base64.b64decode(cursor, altchars=b"-_", validate=True)
                prefix, cursor_work, version = decoded.decode("ascii").split(":")
                if (
                    prefix != "ann"
                    or cursor_work != str(work.id)
                    or not valid_bigint(version)
                ):
                    raise ValueError
            except (UnicodeError, ValueError, binascii.Error) as exc:
                raise invalid("cursor") from exc
            query = query.where(WorkAnnouncement.version < int(version))
        rows = (
            await self.session.scalars(
                query.order_by(WorkAnnouncement.version.desc()).limit(limit + 1)
            )
        ).all()
        next_cursor = None
        if len(rows) > limit:
            key = f"ann:{work.id}:{rows[limit - 1].version}"
            next_cursor = base64.urlsafe_b64encode(key.encode()).decode()
        return {
            "items": await self.announcement_views(work, rows[:limit]),
            "nextCursor": next_cursor,
        }

    async def publish_announcement(self, work_id, body, idempotency_key):
        """在同一事务内分配公告序号并通知发布时的关注者。"""
        work = await self.managed_work(work_id, publishing=True)
        row = await WorkAnnouncementService.insert_announcement(
            self.session, work.id, self.principal.userId, body, idempotency_key
        )
        result = (await self.announcement_views(work, [row]))[0]
        await self.session.commit()
        return result

    async def change_announcement(
        self, work_id, announcement_id, body, revision, *, withdraw=False
    ):
        """保留公告的发布序号和原有通知关联。"""
        if not valid_bigint(announcement_id):
            raise HTTPException(404, "公告不存在")
        work = await self.managed_work(work_id, publishing=not withdraw)
        row = await WorkAnnouncementService.update_announcement(
            self.session,
            work.id,
            int(announcement_id),
            self.principal.userId,
            body,
            "withdraw" if withdraw else "edit",
            revision,
        )
        result = None if withdraw else (await self.announcement_views(work, [row]))[0]
        await self.session.commit()
        return result

    async def rules(self, work_id):
        """返回当前规则，未设置时返回初始版本。"""
        work = await self.works.visible(work_id)
        return {
            "body": work.rules_body,
            "version": work.rules_version,
            "updatedAt": timestamp(work.rules_updated_at),
        }

    async def save_rules(self, work_id, body):
        """保存作品规则并原子更新确认、通知快照及本地出箱。"""
        work = await self.managed_work(work_id, publishing=True)
        now = utc_now()
        work.rules_body = body
        work.rules_version += 1
        work.rules_updated_at = work.updated_at = now
        actor = self.principal.userId
        await advance_work_ack(
            self.session, work.id, actor, "rules", work.rules_version
        )
        source_id = await self.session.scalar(
            text("SELECT nextval('work_rules_notice_source_seq')")
        )
        await notify_work_followers(
            self.session,
            work_id=work.id,
            event_type="work_rules",
            source_id=source_id,
            created_at=now,
            actor_id=actor,
            rules_version=work.rules_version,
        )
        result = {
            "body": work.rules_body,
            "version": work.rules_version,
            "updatedAt": timestamp(now),
        }
        await self.session.commit()
        return result

    async def acknowledge(self, work_id, kind, version):
        """确认用户实际看到的版本，并同步其对应规则提醒的已读状态。"""
        user_id = self.principal.require_user()
        work = await self.works.visible(work_id, lock=True)
        if kind == "rules":
            if version > work.rules_version:
                raise invalid("version", "规则版本不存在")
        else:
            announcement = await self.session.scalar(
                select(WorkAnnouncement.id).where(
                    WorkAnnouncement.work_id == work.id,
                    WorkAnnouncement.version == version,
                )
            )
            if announcement is None:
                raise invalid("version", "公告版本不存在")
        await advance_work_ack(self.session, work.id, user_id, kind, version)
        if kind == "rules":
            marked = await self.session.execute(
                update(Notification)
                .where(
                    Notification.fluxer_user_id == user_id,
                    Notification.work_id == work.id,
                    Notification.event_type == "work_rules",
                    Notification.event_source_platform == "forum",
                    Notification.read_at.is_(None),
                    Notification.rules_version <= version,
                )
                .values(read_at=utc_now())
            )
            if marked.rowcount:
                await enqueue_notification_change(self.session, user_id)
        await self.session.commit()
