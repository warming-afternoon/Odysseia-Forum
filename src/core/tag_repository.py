import logging
from typing import List, Sequence, cast

from sqlalchemy import case, update, ColumnElement
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models import Tag, Thread

logger = logging.getLogger(__name__)


class TagRepository:
    """封装与 Tag 表相关的数据库操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_tags(
        self, tags_data: dict[int, str], update_names: bool = True
    ) -> List[Tag]:
        """
        根据 Discord 标签 ID 和名称获取原生实体，返回内部 ID。
        """
        if not tags_data:
            return []

        tag_ids = list(tags_data.keys())
        # 批量读取已有实体，避免重复同步通过 INSERT 消耗内部 ID 序列。
        existing_statement = (
            select(Tag)
            .where(cast(ColumnElement, Tag.discord_tag_id).in_(tag_ids))
            .execution_options(populate_existing=True)
        )
        existing = list((await self.session.execute(existing_statement)).scalars())
        existing_ids = {tag.discord_tag_id for tag in existing}

        # 仅更新仍为 DC 来源且名称有变化的实体，不覆盖已转换的自定义标签。
        changed_names = {
            tag.discord_tag_id: tags_data[tag.discord_tag_id]
            for tag in existing
            if update_names
            and tag.source == "discord"
            and tag.discord_tag_id is not None
            and tag.name != tags_data[tag.discord_tag_id]
        }
        if changed_names:
            await self.session.execute(
                update(Tag)
                .where(
                    cast(ColumnElement, Tag.discord_tag_id).in_(changed_names),
                    Tag.source == "discord",
                )
                .values(name=case(changed_names, value=Tag.discord_tag_id))
                .execution_options(synchronize_session=False)
            )

        # 只插入缺失实体；保留唯一冲突处理，兼容并发首次发现同一 DC 标签。
        values_to_insert = [
            {
                "discord_tag_id": tag_id,
                "name": name,
                "source": "discord",
                "enabled": True,
            }
            for tag_id, name in tags_data.items()
            if tag_id not in existing_ids
        ]
        if values_to_insert:
            insert_stmt = pg_insert(Tag).values(values_to_insert)
            if update_names:
                write_stmt = insert_stmt.on_conflict_do_update(
                    index_elements=[Tag.discord_tag_id],
                    set_={"name": insert_stmt.excluded.name},
                    where=Tag.source == "discord",
                )
            else:
                write_stmt = insert_stmt.on_conflict_do_nothing(
                    index_elements=[Tag.discord_tag_id]
                )
            await self.session.execute(write_stmt)

        # 查询所有相关的标签对象
        final_statement = (
            select(Tag)
            .where(cast(ColumnElement, Tag.discord_tag_id).in_(tag_ids))
            .execution_options(populate_existing=True)
        )
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
