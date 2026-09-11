from sqlalchemy import and_, or_, select

from models import Tag, Thread, ThreadTagLink
from models.custom_tag_binding import CustomTagBinding
from shared.enum.tag_category import TagCategory


def tag_filters(kind, target_column, included, excluded, logic="and"):
    """按统一内部 ID 筛选目标实际绑定的原生和自定义标签。"""

    def matches(ids):
        """合并同一批 ID 在两类绑定中的命中结果。"""
        custom = (
            select(CustomTagBinding.id)
            .join(Tag, Tag.id == CustomTagBinding.tag_id)
            .where(
                CustomTagBinding.target_type == kind,
                CustomTagBinding.target_id == target_column,
                CustomTagBinding.ended_at.is_(None),
                Tag.deleted_at.is_(None),
                Tag.id.in_(ids),
            )
            .exists()
        )
        if kind != "thread":
            return custom
        # 使用独立帖子别名，防止与外层搜索的 Thread 发生错误关联。
        native_thread = Thread.__table__.alias("native_tag_thread")  # type: ignore[attr-defined]
        native = (
            select(ThreadTagLink.thread_id)
            .join(native_thread, native_thread.c.id == ThreadTagLink.thread_id)
            .join(Tag, Tag.id == ThreadTagLink.tag_id)
            .where(
                native_thread.c.thread_id == target_column,
                Tag.source == "discord",
                Tag.id.in_(ids),
            )
            .exists()
        )
        return or_(native, custom)

    conditions = []
    if included:
        clauses = [matches([int(tag_id)]) for tag_id in set(included)]
        conditions.append(or_(*clauses) if logic == "or" else and_(*clauses))
    if excluded:
        conditions.append(~matches([int(v) for v in excluded]))
    return conditions


async def load_custom_tags(session, kind, target_ids):
    """批量读取标签展示数据，避免逐目标查询和缓存陈旧票数。"""
    if not target_ids:
        return {}
    statement = (
        select(CustomTagBinding, Tag)
        .join(Tag, Tag.id == CustomTagBinding.tag_id)
        .where(
            CustomTagBinding.target_type == kind,
            CustomTagBinding.target_id.in_(target_ids),
            CustomTagBinding.ended_at.is_(None),
            Tag.deleted_at.is_(None),
        )
        .order_by(Tag.category, Tag.name)
    )
    result = {}
    for binding, tag in (await session.execute(statement)).all():
        result.setdefault(binding.target_id, []).append(
            {
                "id": str(tag.id),
                "name": tag.name,
                "category": tag.category,
                "category_name": TagCategory(tag.category).name,
                "source": "custom",
                "enabled": tag.enabled,
                "binding_id": str(binding.id),
                "upvotes": binding.upvotes,
                "downvotes": binding.downvotes,
            }
        )
    return result
