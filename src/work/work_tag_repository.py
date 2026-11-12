"""作品标签候选查询仓储。"""

from sqlalchemy import or_, select
from models.tag import Tag
from models.tag_alias import TagAlias


class WorkTagRepository:
    """读取可新增绑定的统一标签，不按原生平台来源拆分候选。"""

    def __init__(self, session):
        """复用调用方会话。"""
        self.session = session

    async def candidates(self, query, can_view_abyss, offset=0, limit=100):
        """分页读取启用标签，标准名和别名均可命中且不重复返回。"""
        statement = select(Tag).where(Tag.deleted_at.is_(None), Tag.enabled.is_(True))
        if query:
            # 子查询避免一个标签因多个匹配别名而占用多个分页位置。
            alias_match = select(TagAlias.tag_id).where(
                TagAlias.tag_id == Tag.id,
                TagAlias.name.ilike("%" + query + "%"),
            ).exists()
            statement = statement.where(or_(Tag.name.ilike("%" + query + "%"), alias_match))
        if not can_view_abyss:
            statement = statement.where(Tag.is_abyss.is_(False))
        return (
            await self.session.execute(
                statement.order_by(Tag.name, Tag.id).offset(offset).limit(limit + 1)
            )
        ).scalars().all()
