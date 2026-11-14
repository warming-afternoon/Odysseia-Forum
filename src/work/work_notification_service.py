"""新旧通知共用事实、分页和分类可见性。"""

import base64
import binascii
import json
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, update, func, and_, or_, case
from sqlalchemy.orm import aliased
from dto.work.work_command_qo import WorkCommandQo
from shared.time_utils import utc_now
from models.notification import Notification
from models.thread import Thread
from models.work import Work
from models.work_announcement import WorkAnnouncement
from models.thread_update import ThreadUpdate
from work.notification_outbox import enqueue_notification_change


def _encode_cursor(created_at: datetime, notification_id: int) -> str:
    raw = json.dumps({"at": created_at.isoformat(), "id": str(notification_id)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(value: str) -> tuple[datetime, int]:
    try:
        if not value or len(value) > 256:
            raise ValueError
        raw = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        data = json.loads(raw)
        if not isinstance(data, dict) or set(data) != {"at", "id"}:
            raise ValueError
        identity = data["id"]
        if not isinstance(identity, str) or not identity.isascii() or not identity.isdecimal():
            raise ValueError
        identifier = int(identity)
        if not 1 <= identifier <= 2**63 - 1 or not isinstance(data["at"], str):
            raise ValueError
        at = datetime.fromisoformat(data["at"])
        if at.tzinfo is not None:
            at = at.astimezone(timezone.utc).replace(tzinfo=None)
        return at, identifier
    except (ValueError, TypeError, KeyError, OverflowError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(400, "通知分页游标无效") from exc


class WorkNotificationService:
    """读取同一通知行，兼容旧更新、作者新作及标签审核。"""

    def __init__(self, sessions):
        """复用由路由注入的会话工厂。"""
        self.sessions = sessions

    def _visible_query(self, principal):
        """统一列表、计数和已读操作的接收者及可见性范围。"""
        recipient = or_(
            Notification.fluxer_user_id == principal["id"],
            and_(
                Notification.fluxer_user_id.is_(None),
                Notification.user_id == int(principal["discord_user_id"]),
            ),
        )
        legacy_work = (
            select(Thread.work_id)
            .where(
                Thread.thread_id
                == func.coalesce(
                    Notification.thread_id,
                    case(
                        (
                            Notification.target_type == "thread",
                            Notification.target_id,
                        )
                    ),
                ),
                Thread.platform == Notification.event_source_platform,
            )
            .order_by(Thread.id)
            .limit(1)
            .correlate(Notification)
            .scalar_subquery()
        )
        work_id = func.coalesce(
            Notification.work_id,
            WorkAnnouncement.work_id,
            ThreadUpdate.work_id,
            case((Notification.target_type == "work", Notification.target_id)),
            legacy_work,
        )
        duplicate = aliased(Notification)
        legacy_duplicate = (
            select(duplicate.id)
            .where(
                duplicate.event_type == "thread_update",
                duplicate.event_source_platform == "discord",
                duplicate.event_source_id == WorkAnnouncement.legacy_update_id,
                or_(
                    and_(
                        duplicate.fluxer_user_id.is_not(None),
                        duplicate.fluxer_user_id == Notification.fluxer_user_id,
                    ),
                    and_(
                        duplicate.user_id.is_not(None),
                        duplicate.user_id == Notification.user_id,
                    ),
                ),
            )
            .correlate(Notification, WorkAnnouncement)
            .exists()
        )
        abyss_config = principal.get("config", {}).get("abyss")
        abyss_column = Work.is_abyss
        if abyss_config is not None:
            channels = [str(value) for value in abyss_config.get("channel_ids", [])]
            abyss_column = case(
                (
                    Work.source_platform == "discord",
                    Work.source_channel_id.in_(channels),
                ),
                else_=Work.is_abyss,
            )
        base = (
            select(Notification, WorkAnnouncement, ThreadUpdate, Work)
            .outerjoin(
                WorkAnnouncement,
                or_(
                    and_(
                        Notification.event_type == "work_announcement",
                        WorkAnnouncement.id == Notification.event_source_id,
                    ),
                    and_(
                        Notification.event_type == "thread_update",
                        Notification.event_source_platform == "discord",
                        WorkAnnouncement.legacy_update_id
                        == Notification.event_source_id,
                    ),
                ),
            )
            .outerjoin(
                ThreadUpdate,
                and_(
                    Notification.event_type == "thread_update",
                    Notification.event_source_platform == "discord",
                    ThreadUpdate.id == Notification.event_source_id,
                ),
            )
            .outerjoin(Work, Work.id == work_id)
            .where(
                recipient,
                or_(
                    and_(
                        Notification.event_type == "tag_review",
                        Notification.target_type == "booklist",
                        work_id.is_(None),
                    ),
                    and_(
                        Work.id.is_not(None),
                        Work.deleted_at.is_(None),
                        Work.source_visible.is_(True),
                        or_(Work.is_nsfw.is_(False), principal["show_nsfw"]),
                        or_(abyss_column.is_(False), principal["can_view_abyss"]),
                    ),
                ),
                ~and_(
                    Notification.event_type == "work_announcement",
                    WorkAnnouncement.legacy_update_id.is_not(None),
                    legacy_duplicate,
                ),
            )
        )
        return base

    async def notifications(
        self, offset: int, limit: int, command: WorkCommandQo,
        unread_only: bool = False, author_id: int | None = None,
        cursor: str | None = None,
    ):
        """关联旧来源与公告，分类过滤同时作用于记录、总数及未读数。"""
        principal = command.actor
        offset, limit = max(0, offset), min(100, max(1, limit))
        async with self.sessions() as session:
            base = self._visible_query(principal)
            if author_id is not None:
                # V2 作者筛选使用 Fluxer 统一身份，不能将同值 Discord ID 当作同一用户。
                base = base.where(Work.author_id == str(author_id))
            if unread_only:
                base = base.where(Notification.read_at.is_(None))
            total = (
                await session.execute(select(func.count()).select_from(base.subquery()))
            ).scalar_one()
            unread = (
                await session.execute(
                    select(func.count()).select_from(
                        base.where(Notification.read_at.is_(None)).subquery()
                    )
                )
            ).scalar_one()
            page = base
            if cursor is not None:
                created_at, cursor_id = _decode_cursor(cursor)
                page = page.where(or_(
                    Notification.created_at < created_at,
                    and_(Notification.created_at == created_at, Notification.id < cursor_id),
                ))
            rows = (
                await session.execute(
                    page.order_by(Notification.created_at.desc(), Notification.id.desc())
                    .offset(0 if cursor is not None else offset)
                    .limit(limit + 1)
                )
            ).all()
            has_more = len(rows) > limit
            rows = rows[:limit]
            next_cursor = _encode_cursor(rows[-1][0].created_at, rows[-1][0].id) if has_more else None
            # 作品投影批量加载展示资料，复用现有卡片和预览所需字段。
            from work.work_service import WorkService

            unique_works = {work.id: work for _, _, _, work in rows if work}
            projected = await WorkService(session).project(list(unique_works.values()))
            work_views = {str(work["id"]): work for work in projected}
            items = []
            for notice, announcement, legacy, work in rows:
                withdrawn = bool(announcement and announcement.withdrawn_at)
                title = (
                    announcement.title
                    if announcement
                    else (
                        legacy.version
                        if legacy
                        else (work.title if work else "标签审核")
                    )
                )
                body = (
                    announcement.body
                    if announcement
                    else (
                        legacy.description
                        if legacy
                        else (
                            "关注的作者发布了新作品"
                            if notice.event_type == "author_new_thread"
                            else "有新的标签申请待审核"
                        )
                    )
                )
                items.append(
                    {
                        "id": str(notice.id),
                        "type": notice.event_type,
                        "work_id": str(work.id) if work else None,
                        "work_title": work.title if work else None,
                        "author": work_views.get(str(work.id), {}).get("author") if work else None,
                        "work": work_views.get(str(work.id)) if work else None,
                        "announcement_id": str(announcement.id)
                        if announcement
                        else None,
                        "title": None if withdrawn else title,
                        "body": "公告已撤回" if withdrawn else body,
                        "withdrawn": withdrawn,
                        "target_type": notice.target_type,
                        "target_id": str(notice.target_id)
                        if notice.target_id is not None
                        else None,
                        "proposal_id": str(notice.event_source_id)
                        if notice.event_type == "tag_review"
                        else None,
                        "read_at": notice.read_at,
                        "created_at": notice.created_at,
                    }
                )
            return {
                "items": items,
                "total": total,
                "unread_count": unread,
                "offset": offset,
                "limit": limit,
                "next_cursor": next_cursor,
            }

    async def unread_count(self, command: WorkCommandQo):
        """只统计当前可见且未读的通知。"""
        async with self.sessions() as session:
            base = self._visible_query(command.actor).where(
                Notification.read_at.is_(None)
            )
            count = (
                await session.execute(select(func.count()).select_from(base.subquery()))
            ).scalar_one()
            return {"unread_count": count}

    async def read_notification(self, notification_id: int, command: WorkCommandQo):
        """仅标记当前可见的通知，与 V1 共享已读事实。"""
        async with self.sessions() as session:
            visible = self._visible_query(command.actor).where(
                Notification.id == notification_id
            )
            visible_ids = select(visible.subquery().c.id)
            result = await session.execute(
                update(Notification)
                .where(Notification.id.in_(visible_ids), Notification.read_at.is_(None))
                .values(read_at=utc_now())
            )
            if result.rowcount:
                await enqueue_notification_change(session, command.actor["id"])
            await session.commit()
        return {"read": True}

    async def read_all(self, command: WorkCommandQo):
        """在单条 SQL 的事务快照内标记可见通知，排除并发的新通知。"""
        async with self.sessions() as session:
            visible = self._visible_query(command.actor).where(
                Notification.read_at.is_(None)
            )
            visible_ids = select(visible.subquery().c.id)
            result = await session.execute(
                update(Notification)
                .where(Notification.id.in_(visible_ids), Notification.read_at.is_(None))
                .values(read_at=utc_now())
            )
            marked_read = max(0, int(result.rowcount or 0))
            if marked_read:
                await enqueue_notification_change(session, command.actor["id"])
            await session.commit()
        return {"read": True, "marked_read": marked_read}
