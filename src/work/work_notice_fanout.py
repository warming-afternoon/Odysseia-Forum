"""事务内写入面向作品关注者的 Forum 本地动态。"""

from sqlalchemy import BigInteger, String, case, cast, func, literal, or_, select
from sqlalchemy.dialects.postgresql import insert

from models.discord_identity import DiscordIdentity
from models.notification import Notification
from models.thread import Thread
from models.thread_follow import ThreadFollow
from models.work import Work
from platform_gateway.policy import author_hidden
from work.notification_outbox import execute_fanout_with_outbox


async def notify_work_followers(
    session,
    *,
    work_id,
    event_type,
    source_id,
    created_at,
    actor_id,
    actor_discord_id=None,
    rules_version=None,
):
    """对当前有效关注者批量写入本地通知和 WebSocket 出箱。"""
    if (
        event_type not in {"work_rules", "work_announcement"}
        or (
            event_type == "work_rules"
            and (not work_id or type(rules_version) is not int or rules_version <= 0)
        )
        or (event_type != "work_rules" and rules_version is not None)
    ):
        raise ValueError("作品动态类型或规则版本无效")

    discord_threads = select(Thread.thread_id).where(
        Thread.work_id == work_id,
        Thread.platform == "discord",
    )
    visible_discord_thread = (
        discord_threads.where(Thread.deleted_at.is_(None), Thread.show_flag.is_(True))
        .order_by(Thread.id)
        .limit(1)
        .scalar_subquery()
    )
    # 平台与历史 Discord 关注都按当前身份排除作者和操作人。
    author_id = select(Work.author_id).where(Work.id == work_id).scalar_subquery()
    visible_work = (
        select(Work.id)
        .where(
            Work.id == work_id,
            Work.deleted_at.is_(None),
            Work.source_visible.is_(True),
            or_(Work.source_platform != "platform", Work.published_at.is_not(None)),
            ~author_hidden(Work.author_id),
        )
        .exists()
    )
    recipient = func.coalesce(
        ThreadFollow.fluxer_user_id,
        case((DiscordIdentity.is_current.is_(True), DiscordIdentity.user_id)),
    )
    discord_recipient = ThreadFollow.user_id
    if event_type == "work_rules":
        # 规则通知需要统一接收人，才能在确认时精确标为已读。
        discord_recipient = literal(None, BigInteger)
    followers = (
        select(
            discord_recipient,
            recipient,
            literal(event_type),
            literal(source_id, BigInteger),
            literal(work_id, BigInteger),
            literal(created_at),
            literal("forum"),
            visible_discord_thread,
            literal(rules_version, BigInteger),
        )
        .select_from(ThreadFollow)
        .outerjoin(
            DiscordIdentity,
            DiscordIdentity.discord_user_id == cast(ThreadFollow.user_id, String),
        )
        .where(
            visible_work,
            ThreadFollow.active_flag.is_(True),
            ThreadFollow.followed_at <= created_at,
            recipient.is_distinct_from(actor_id),
            DiscordIdentity.user_id.is_distinct_from(actor_id),
            or_(
                author_id.is_(None),
                recipient.is_distinct_from(author_id)
                & DiscordIdentity.user_id.is_distinct_from(author_id),
            ),
        )
    )
    if event_type == "work_rules":
        followers = followers.where(recipient.is_not(None))
    if actor_discord_id is not None:
        followers = followers.where(
            ThreadFollow.user_id.is_distinct_from(actor_discord_id)
        )
    # 两个分支分别使用作品与旧帖索引，避免扫描所有旧关注记录。
    recipients = followers.where(ThreadFollow.work_id == work_id).union_all(
        followers.where(
            ThreadFollow.work_id.is_(None),
            ThreadFollow.thread_id.in_(discord_threads),
        )
    )
    statement = (
        insert(Notification)
        .from_select(
            [
                "user_id",
                "fluxer_user_id",
                "event_type",
                "event_source_id",
                "work_id",
                "created_at",
                "event_source_platform",
                "thread_id",
                "rules_version",
            ],
            recipients,
        )
        .on_conflict_do_nothing()
    )
    await execute_fanout_with_outbox(session, statement)
