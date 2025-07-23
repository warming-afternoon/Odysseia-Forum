from typing import List, Sequence
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.models.thread import Thread
from shared.models.tag import Tag

class TagSystemRepository:
    """封装与标签系统相关的数据库操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_tags(self, tag_names: List[str]) -> List[Tag]:
        """
        根据标签名称列表，获取或创建标签对象。
        """
        if not tag_names:
            return []

        # 查询已存在的标签
        statement = select(Tag).where(Tag.name.in_(tag_names))
        result = await self.session.exec(statement)
        existing_tags = result.all()
        existing_tag_names = {tag.name for tag in existing_tags}

        # 找出需要创建的新标签
        new_tag_names = [name for name in tag_names if name not in existing_tag_names]
        new_tags = [Tag(name=name) for name in new_tag_names]

        if new_tags:
            self.session.add_all(new_tags)
            await self.session.flush()  # 刷新以获取新标签的ID

        return list(existing_tags) + new_tags

    async def add_or_update_thread_with_tags(self, thread_data: dict, tag_names: List[str]):
        """
        原子性地添加或更新一个帖子及其标签。
        """
        # 查找现有帖子
        statement = select(Thread).where(Thread.thread_id == thread_data['thread_id']).options(selectinload(Thread.tags))
        result = await self.session.exec(statement)
        db_thread = result.first()

        # 获取或创建所有相关标签
        tags = await self.get_or_create_tags(tag_names)

        if db_thread:
            # 更新帖子
            for key, value in thread_data.items():
                setattr(db_thread, key, value)
            db_thread.tags = tags
            self.session.add(db_thread)
        else:
            # 创建新帖子
            new_thread = Thread(**thread_data)
            new_thread.tags = tags
            self.session.add(new_thread)
        
        await self.session.commit()

    async def get_indexed_channel_ids(self) -> Sequence[int]:
        """获取所有已索引的频道ID列表"""
        statement = select(Thread.channel_id).distinct()
        result = await self.session.exec(statement)
        return result.all()

    async def delete_thread_index(self, thread_id: int):
        """删除帖子的所有相关索引数据"""
        statement = select(Thread).where(Thread.thread_id == thread_id)
        result = await self.session.exec(statement)
        db_thread = result.first()
        if db_thread:
            await self.session.delete(db_thread)
            await self.session.commit()

    async def get_tags_for_author(self, author_id: int) -> Sequence[Tag]:
        """获取指定作者发布过的所有帖子的唯一标签列表"""
        statement = (
            select(Tag)
            .join(Thread, Tag.threads)
            .where(Thread.author_id == author_id)
            .distinct()
        )
        result = await self.session.exec(statement)
        return result.all()
    
    async def get_tags_for_channels(self, channel_ids: List[int]) -> Sequence[Tag]:
        """获取指定频道列表内的所有唯一标签"""
        statement = (
            select(Tag)
            .join(Thread, Tag.threads)
            .where(Thread.channel_id.in_(channel_ids))
            .distinct()
        )
        result = await self.session.exec(statement)
        return result.all()