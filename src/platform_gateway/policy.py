"""平台作品可见性与聊天策略。"""

from sqlalchemy import (
    BigInteger,
    Boolean,
    String,
    and_,
    case,
    column,
    exists,
    func,
    or_,
    select,
    table,
)

from core.tag_query import tag_filters
from models.booklist import Booklist
from models.tag import Tag
from models.tag_binding import TagBinding
from models.work import Work
from models.work_native_tag_binding import WorkNativeTagBinding
from models.work_native_tag_source import WorkNativeTagSource


hidden_author = table(
    "platform_hidden_author", column("user_id", String), column("hidden", Boolean)
)
projection = table(
    "platform_work_projection",
    column("consumer", String),
    column("work_id", BigInteger),
    column("state", String),
)
RATINGS = ("general", "r18", "r18g")
RATING_NAMES = {"general": "全年龄", "r18": "R18", "r18g": "R18G"}
RATING_IDS = {f"rating:{name}": rating for rating, name in RATING_NAMES.items()}


def tagged(predicate):
    """查询有效本地绑定和有效 Discord 来源绑定。"""
    local = (
        select(TagBinding.work_id)
        .join(Tag, Tag.id == TagBinding.tag_id)
        .where(
            TagBinding.work_id == Work.id,
            TagBinding.binding_source == "local",
            TagBinding.ended_at.is_(None),
            Tag.deleted_at.is_(None),
            predicate,
        )
    )
    native = (
        select(WorkNativeTagBinding.work_id)
        .join(
            WorkNativeTagSource,
            WorkNativeTagSource.id == WorkNativeTagBinding.source_id,
        )
        .join(Tag, Tag.id == WorkNativeTagSource.tag_id)
        .where(
            WorkNativeTagBinding.work_id == Work.id,
            WorkNativeTagSource.platform == "discord",
            Tag.deleted_at.is_(None),
            predicate,
        )
    )
    return or_(local.exists(), native.exists())


def rating_expression():
    """既有 R18 作品沿用源站分区，新平台分级以元信息为准。"""
    return func.coalesce(
        Work.rating, case((Work.is_nsfw.is_(True), "r18"), else_="general")
    )


def author_hidden(user_column):
    """判定权威隐藏投影，缺少记录时默认为可见。"""
    return exists(
        select(hidden_author.c.user_id).where(
            hidden_author.c.user_id == user_column, hidden_author.c.hidden.is_(True)
        )
    )


def visible_booklist_conditions(principal):
    """对书单页面和作品的收藏状态使用相同的屏蔽与分级规则。"""
    owner = Booklist.fluxer_owner_id
    public = Booklist.is_public.is_(True)
    conditions = [
        Booklist.merged_into_id.is_(None),
        Booklist.deleted_at.is_(None),
        ~author_hidden(owner),
        or_(public, owner == principal.userId) if principal.userId else public,
    ]
    if principal.mode != "r18":
        abyss_tags = (
            select(TagBinding.id)
            .join(Tag, Tag.id == TagBinding.tag_id)
            .where(
                TagBinding.target_type == "booklist",
                TagBinding.target_id == Booklist.id,
                TagBinding.ended_at.is_(None),
                Tag.deleted_at.is_(None),
                Tag.is_abyss.is_(True),
            )
        )
        conditions.extend((Booklist.rating == "general", ~abyss_tags.exists()))
    elif not principal.showR18G:
        conditions.append(Booklist.rating != "r18g")
    if principal.blockedUserIds:
        conditions.append(or_(owner.is_(None), owner.not_in(principal.blockedUserIds)))
    blocked = [value for value in principal.blockedTagIds if valid_bigint(value)]
    conditions.extend(tag_filters("booklist", Booklist.id, None, blocked))
    ratings = [
        RATING_IDS[value] for value in principal.blockedTagIds if value in RATING_IDS
    ]
    if ratings:
        conditions.append(Booklist.rating.not_in(ratings))
    return conditions


def visible_conditions(principal, *, consumer=None, own_draft=False, trash=False):
    """全部可见性条件写在列表、分页和计数共用的 SQL 中。"""
    draft = and_(
        Work.source_platform == "platform",
        Work.published_at.is_(None),
    )
    source_visible = and_(Work.source_visible.is_(True), ~draft)
    if own_draft and principal.userId:
        source_visible = or_(
            source_visible,
            and_(draft, Work.author_id == principal.userId),
        )
    conditions = (
        [Work.deleted_at.is_not(None)]
        if trash
        else [source_visible, Work.deleted_at.is_(None)]
    )
    if not trash and not principal.has_any(
        "works.manage_any", "moderation.content", "trash.manage"
    ):
        can_read = ~author_hidden(Work.author_id)
        if principal.userId:
            can_read = or_(can_read, Work.author_id == principal.userId)
        conditions.append(can_read)
    if principal.mode != "r18":
        conditions.extend(
            (
                rating_expression() == "general",
                Work.is_nsfw.is_(False),
                Work.is_abyss.is_(False),
                ~tagged(Tag.is_abyss.is_(True)),
            )
        )
    elif not principal.showR18G:
        conditions.append(rating_expression() != "r18g")
    if not trash and principal.blockedUserIds:
        conditions.append(
            or_(
                Work.author_id.is_(None),
                Work.author_id.not_in(principal.blockedUserIds),
            )
        )
    blocked = [int(value) for value in principal.blockedTagIds if valid_bigint(value)]
    if not trash and blocked:
        conditions.append(~tagged(Tag.id.in_(blocked)))
    blocked_ratings = [
        RATING_IDS[value] for value in principal.blockedTagIds if value in RATING_IDS
    ]
    if not trash and blocked_ratings:
        conditions.append(rating_expression().not_in(blocked_ratings))
    if consumer:
        conditions.append(
            ~exists(
                select(projection.c.work_id).where(
                    projection.c.consumer == consumer,
                    projection.c.work_id == Work.id,
                    projection.c.state.in_(("deleted", "purged")),
                )
            )
        )
    return conditions


def valid_bigint(value):
    """校验未受信任的数字 ID，防止越过数据库字段范围。"""
    return (
        len(value) <= 19
        and value.isascii()
        and value.isdecimal()
        and 0 < int(value) < 2**63
    )


async def chat_policy(session, work):
    """把作品正本的当前访问策略转换为聊天回查与同步载荷。"""
    hidden, restricted_tag = (
        await session.execute(
            select(
                author_hidden(Work.author_id),
                tagged(Tag.is_abyss.is_(True)),
            )
            .select_from(Work)
            .where(Work.id == work.id)
        )
    ).one()
    rating = work.rating or ("r18" if work.is_nsfw else "general")
    restricted = bool(
        work.is_nsfw or work.is_abyss or restricted_tag or rating != "general"
    )
    return {
        "workId": str(work.id),
        "title": work.title,
        "chatTitle": work.chat_title,
        "authorId": work.author_id,
        "managerIds": [],
        "chatManagerIds": [],
        "rating": rating,
        "restricted": restricted,
        "visibility": "restricted" if restricted else "public",
        "hidden": bool(hidden or not work.source_visible and not work.deleted_at),
        "deleted": bool(work.deleted_at),
        "disabled": {"attach": False, "poll": False, "fun": False},
    }
