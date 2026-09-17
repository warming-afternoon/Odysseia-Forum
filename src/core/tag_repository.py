from core.discord_tag_source_repository import DiscordTagSourceRepository
import logging
from typing import List, Sequence, cast

from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from models import Tag, Thread

logger = logging.getLogger(__name__)


class TagRepository:
    """封装与 Tag 表相关的数据库操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_tags(
        self,
        tags_data: dict[int, str],
        update_names: bool = True,
        channel_id: int | None = None,
    ) -> List[Tag]:
        """解析原生来源返回标准概念；名称更新仅由完整频道快照负责。"""
        sources = await DiscordTagSourceRepository(self.session).ensure(
            tags_data, channel_id
        )
        if not sources:
            return []
        result = await self.session.execute(
            select(Tag).where(Tag.id.in_({s.tag_id for s in sources}))
        )
        return list(result.scalars())

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
        if tag and tag.source == "custom" and tag.deleted_at is None:
            tag.name = new_name
            self.session.add(tag)
            await self.session.commit()
