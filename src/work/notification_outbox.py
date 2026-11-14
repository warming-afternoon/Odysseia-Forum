"""事务内登记通知变化；投递由独立发布器负责。"""
from sqlalchemy import String, cast, column, func, select, table, text
from sqlalchemy.dialects.postgresql import insert

from models.discord_identity import DiscordIdentity
from models.notification import Notification


async def enqueue_notification_change(session, recipient_id: str) -> None:
    """由调用者在与业务变更相同的事务中提交。"""
    await session.execute(
        text("INSERT INTO work_notification_outbox (recipient_id) VALUES (:recipient_id)"),
        {"recipient_id": recipient_id},
    )


async def execute_fanout_with_outbox(session, statement) -> int:
    """批量插入通知并为有当前统一身份的实际接收者登记变化。"""
    inserted = statement.returning(
        Notification.user_id, Notification.fluxer_user_id
    ).cte("inserted_notifications")
    recipient = func.coalesce(inserted.c.fluxer_user_id, DiscordIdentity.user_id)
    outbox = table("work_notification_outbox", column("recipient_id", String))
    changes = (
        insert(outbox)
        .from_select(
            ["recipient_id"],
            select(recipient)
            .select_from(
                inserted.outerjoin(
                    DiscordIdentity,
                    (DiscordIdentity.discord_user_id == cast(inserted.c.user_id, String))
                    & DiscordIdentity.is_current.is_(True),
                )
            )
            .where(recipient.is_not(None)),
        )
        .returning(outbox.c.recipient_id)
        .cte("notification_changes")
    )
    result = await session.execute(
        select(func.count()).select_from(inserted).add_cte(changes)
    )
    return int(result.scalar_one())


async def enqueue_announcement_invalidation(session, announcement) -> None:
    """公告修改仅刷新既有接收者，不创建新的业务通知。"""
    from sqlalchemy import and_, or_

    source = and_(
        Notification.event_type == "work_announcement",
        Notification.event_source_id == announcement.id,
    )
    if announcement.legacy_update_id is not None:
        source = or_(
            source,
            and_(
                Notification.event_type == "thread_update",
                Notification.event_source_platform == "discord",
                Notification.event_source_id == announcement.legacy_update_id,
            ),
        )
    recipient = func.coalesce(Notification.fluxer_user_id, DiscordIdentity.user_id)
    outbox = table("work_notification_outbox", column("recipient_id", String))
    recipients = (
        select(recipient)
        .select_from(Notification)
        .outerjoin(
            DiscordIdentity,
            (DiscordIdentity.discord_user_id == cast(Notification.user_id, String))
            & DiscordIdentity.is_current.is_(True),
        )
        .where(source, recipient.is_not(None))
        .distinct()
    )
    await session.execute(insert(outbox).from_select(["recipient_id"], recipients))
