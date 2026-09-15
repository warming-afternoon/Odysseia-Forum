from sqlalchemy import and_, or_, select

from models import Tag
from dto.custom_tag_binding_response import CustomTagBindingResponse
from models.tag_binding import TagBinding
from shared.enum.tag_category import TagCategory


def tag_filters(kind, target_column, included, excluded, logic="and"):
    """按统一内部 ID 筛选目标实际绑定的原生和自定义标签。"""

    def matches(ids):
        """合并同一批 ID 在两类绑定中的命中结果。"""
        custom = (
            select(TagBinding.id)
            .join(Tag, Tag.id == TagBinding.tag_id)
            .where(
                TagBinding.target_type == kind,
                TagBinding.target_id == target_column,
                TagBinding.ended_at.is_(None),
                Tag.deleted_at.is_(None),
                Tag.id.in_(ids),
            )
            .exists()
        )
        return custom

    conditions = []
    if included:
        clauses = [matches([int(tag_id)]) for tag_id in set(included)]
        conditions.append(or_(*clauses) if logic == "or" else and_(*clauses))
    if excluded:
        conditions.append(~matches([int(v) for v in excluded]))
    return conditions


async def load_custom_tags(session, kind, target_ids) -> dict[int, list[CustomTagBindingResponse]]:
    """批量读取标签展示数据，避免逐目标查询和缓存陈旧票数。"""
    if not target_ids:
        return {}
    statement = (
        select(TagBinding, Tag)
        .join(Tag, Tag.id == TagBinding.tag_id)
        .where(
            TagBinding.target_type == kind,
            TagBinding.target_id.in_(target_ids),
            TagBinding.ended_at.is_(None),
            TagBinding.binding_source == "local",
            Tag.deleted_at.is_(None),
        )
        .order_by(Tag.category, Tag.name)
    )
    result: dict[int, list[CustomTagBindingResponse]] = {}
    for binding, tag in (await session.execute(statement)).all():
        result.setdefault(binding.target_id, []).append(
            CustomTagBindingResponse(
                id=str(tag.id),
                name=tag.name,
                category=tag.category,
                category_name=TagCategory(tag.category).name if tag.category else None,
                source=tag.source,
                enabled=tag.enabled,
                binding_id=str(binding.id),
                upvotes=binding.upvotes,
                downvotes=binding.downvotes,
            )
        )
    return result
