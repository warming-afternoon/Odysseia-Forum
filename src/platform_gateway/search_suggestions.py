"""从用户可见的数据中生成搜索建议。"""

from sqlalchemy import func, or_, select

from models.tag import Tag
from models.tag_binding import TagBinding
from models.user import User
from models.work import Work
from models.work_native_tag_binding import WorkNativeTagBinding
from models.work_native_tag_source import WorkNativeTagSource
from platform_gateway.collection_api_service import CollectionApiService
from platform_gateway.http import invalid
from platform_gateway.policy import visible_conditions
from platform_gateway.work_repository import CATEGORY_GROUPS


async def search_suggestions(service, keyword):
    """作品标题、标签、作者和书单建议使用一致的可见性边界。"""
    keyword = keyword.strip()
    if not keyword or len(keyword) > 200:
        raise invalid("q", "搜索词长度必须为 1 至 200 个字符")
    session = service.session
    principal = service.principal
    works = await service.list({"q": keyword, "limit": "8"}, search=True)
    collections = await CollectionApiService(session, principal).list(
        {"q": keyword, "limit": "8"}
    )
    visible = (
        select(Work.id, Work.author_id)
        .where(*visible_conditions(principal, consumer="search"))
        .cte()
    )
    local = (
        select(TagBinding.work_id, TagBinding.tag_id)
        .join(
            visible,
            visible.c.id == TagBinding.work_id,
        )
        .where(TagBinding.ended_at.is_(None), TagBinding.binding_source == "local")
    )
    native = (
        select(WorkNativeTagBinding.work_id, WorkNativeTagSource.tag_id)
        .join(visible, visible.c.id == WorkNativeTagBinding.work_id)
        .join(
            WorkNativeTagSource,
            WorkNativeTagSource.id == WorkNativeTagBinding.source_id,
        )
        .where(WorkNativeTagSource.platform == "discord")
    )
    matched = local.union(native).subquery()
    count = func.count(matched.c.work_id)
    tags = (
        select(Tag.id, Tag.name, Tag.category, count)
        .join(matched, matched.c.tag_id == Tag.id)
        .where(Tag.deleted_at.is_(None), Tag.name.icontains(keyword, autoescape=True))
        .group_by(Tag.id, Tag.name, Tag.category)
        .order_by(count.desc(), Tag.id.asc())
        .limit(8)
    )
    users = (
        select(User.id, func.coalesce(User.display_name, User.username))
        .where(
            User.merged_into_id.is_(None),
            User.id.in_(select(visible.c.author_id)),
            or_(
                User.display_name.icontains(keyword, autoescape=True),
                User.username.icontains(keyword, autoescape=True),
            ),
        )
        .order_by(User.id.asc())
        .limit(8)
    )
    return {
        "works": [
            {"id": work["id"], "title": work["title"]} for work in works["items"]
        ],
        "tags": [
            {
                "id": str(tag_id),
                "name": name,
                "group": CATEGORY_GROUPS.get(category, "uncategorized"),
                "count": n,
            }
            for tag_id, name, category, n in await session.execute(tags)
        ],
        "users": [
            {"id": user_id, "displayName": label}
            for user_id, label in await session.execute(users)
        ],
        "collections": [
            {"id": row["id"], "title": row["title"]} for row in collections["items"]
        ],
        "lists": [],
        "events": [],
    }
