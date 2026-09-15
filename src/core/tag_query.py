from collections.abc import Sequence
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement
from sqlmodel import col

from dto.custom_tag_binding_response import CustomTagBindingResponse
from models import Tag
from models.tag_binding import TagBinding
from shared.enum.tag_category import TagCategory


def tag_filters(
    kind: str,
    target_column: Any,
    included: Sequence[int | str] | None,
    excluded: Sequence[int | str] | None,
    logic: str = "and",
) -> list[ColumnElement[bool]]:
    """按统一内部 ID 筛选目标实际绑定的原生和自定义标签。"""

    def matches(ids: Sequence[int]) -> ColumnElement[bool]:
        """合并同一批 ID 在两类绑定中的命中结果。"""
        custom = (
            select(col(TagBinding.id))
            .join(Tag, col(Tag.id) == col(TagBinding.tag_id))
            .where(
                col(TagBinding.target_type) == kind,
                col(TagBinding.target_id) == target_column,
                col(TagBinding.ended_at).is_(None),
                col(Tag.deleted_at).is_(None),
                col(Tag.id).in_(ids),
            )
            .exists()
        )
        return custom

    conditions: list[ColumnElement[bool]] = []
    if included:
        clauses = [matches([int(tag_id)]) for tag_id in set(included)]
        conditions.append(or_(*clauses) if logic == "or" else and_(*clauses))
    if excluded:
        conditions.append(~matches([int(v) for v in excluded]))
    return conditions


async def load_custom_tags(
    session: AsyncSession, kind: str, target_ids: Sequence[int]
) -> dict[int, list[CustomTagBindingResponse]]:
    """批量读取标签展示数据，避免逐目标查询和缓存陈旧票数。"""
    if not target_ids:
        return {}
    statement = (
        select(TagBinding, Tag)
        .join(Tag, col(Tag.id) == col(TagBinding.tag_id))
        .where(
            col(TagBinding.target_type) == kind,
            col(TagBinding.target_id).in_(target_ids),
            col(TagBinding.ended_at).is_(None),
            col(TagBinding.binding_source) == "local",
            col(Tag.deleted_at).is_(None),
        )
        .order_by(col(Tag.category), col(Tag.name))
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
