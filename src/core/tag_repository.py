import logging
from typing import List, Sequence, cast

from sqlalchemy import ColumnElement
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models import Tag, Thread

logger = logging.getLogger(__name__)


class TagRepository:
    """封装与 Tag 表相关的数据库操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_tags(self, tags_data: dict[int, str]) -> List[Tag]:
        """
        根据 Discord 标签 ID 和名称获取原生实体，返回内部 ID。
        """
        if not tags_data:
            return []

        tag_ids = list(tags_data.keys())
        values_to_insert = [
            {"discord_tag_id": id, "name": name, "source": "discord", "enabled": True}
            for id, name in tags_data.items()
        ]

        # 使用 INSERT ... ON CONFLICT DO UPDATE 一次性完成创建和更新
        insert_stmt = pg_insert(Tag).values(values_to_insert)

        # 构建 ON CONFLICT ... DO UPDATE 子句
        # Discord ID 冲突只更新名称，不改变内部主键。
        # 'excluded' 是一个特殊的对象，代表了在 INSERT 语句中试图插入的值
        update_stmt = insert_stmt.on_conflict_do_update(
            index_elements=[Tag.discord_tag_id], set_={"name": insert_stmt.excluded.name}
        )

        await self.session.execute(update_stmt)

        # 查询所有相关的标签对象
        final_statement = select(Tag).where(cast(ColumnElement, Tag.discord_tag_id).in_(tag_ids)).execution_options(populate_existing=True)
        result = await self.session.execute(final_statement)
        return list(result.scalars().all())

    async def get_tags_for_channels(self, channel_ids: List[int]) -> Sequence[Tag]:
        """获取指定频道列表内的所有唯一标签"""
        statement = (
            select(Tag)
            .join(Thread, Tag.threads)  # type: ignore
            .where(cast(ColumnElement, Thread.channel_id).in_(channel_ids))
            .distinct()
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_all_tags(self) -> Sequence[Tag]:
        """获取数据库中所有的标签。"""
        statement = select(Tag)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_all_unique_tags_from_indexed_threads(self) -> Sequence[Tag]:
        """获取所有已索引帖子中的唯一标签"""
        statement = select(Tag).join(Thread, Tag.threads).distinct()  # type: ignore
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def update_tag_name(self, tag_id: int, new_name: str):
        """更新指定ID的标签的名称。"""
        statement = select(Tag).where(Tag.id == tag_id)  # type: ignore
        result = await self.session.execute(statement)
        tag = result.scalars().first()
        if tag:
            tag.name = new_name
            self.session.add(tag)
            await self.session.commit()
