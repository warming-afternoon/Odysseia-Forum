from typing import List, Sequence
from shared.models.tag_vote import TagVote
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
        result = await self.session.execute(statement)
        existing_tags = result.scalars().all()
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
        result = await self.session.execute(statement)
        db_thread = result.scalars().first()

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
        result = await self.session.execute(statement)
        return result.all()

    async def delete_thread_index(self, thread_id: int):
        """删除帖子的所有相关索引数据"""
        statement = select(Thread).where(Thread.thread_id == thread_id)
        result = await self.session.execute(statement)
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
        result = await self.session.execute(statement)
        return result.all()
    
    async def get_tags_for_channels(self, channel_ids: List[int]) -> Sequence[Tag]:
        """获取指定频道列表内的所有唯一标签"""
        statement = (
            select(Tag)
            .join(Thread, Tag.threads)
            .where(Thread.channel_id.in_(channel_ids))
            .distinct()
        )
        result = await self.session.execute(statement)
        return result.all()
    
    async def _update_thread_vote_summary(self, thread_id: int):
        """
        内部方法：重新计算并更新一个帖子的标签投票摘要。
        """
        # 查找帖子
        thread_statement = select(Thread).where(Thread.thread_id == thread_id)
        thread_result = await self.session.execute(thread_statement)
        db_thread = thread_result.first()
        if not db_thread:
            return

        # 查询该帖子的所有投票
        vote_statement = select(TagVote).where(TagVote.thread_id == db_thread.id)
        vote_result = await self.session.execute(vote_statement)
        all_votes = vote_result.all()

        # 计算摘要
        summary = {}
        for vote in all_votes:
            if vote.tag_id not in summary:
                summary[vote.tag_id] = {"up": 0, "down": 0, "score": 0}
            
            if vote.vote == 1:
                summary[vote.tag_id]["up"] += 1
            elif vote.vote == -1:
                summary[vote.tag_id]["down"] += 1
            
            summary[vote.tag_id]["score"] += vote.vote
        
        db_thread.tag_votes_summary = summary
        self.session.add(db_thread)
        await self.session.flush()

    async def record_tag_vote(self, user_id: int, thread_id: int, tag_id: int, vote_value: int):
        """
        记录用户对特定帖子中特定标签的投票。
        """
        # 查找帖子ID
        thread_statement = select(Thread.id).where(Thread.thread_id == thread_id)
        thread_result = await self.session.execute(thread_statement)
        db_thread_id = thread_result.first()
        if not db_thread_id:
            # 如果帖子不存在于数据库中，则无法投票
            return

        # 查找现有投票
        statement = select(TagVote).where(
            TagVote.user_id == user_id,
            TagVote.thread_id == db_thread_id,
            TagVote.tag_id == tag_id
        )
        result = await self.session.execute(statement)
        existing_vote = result.first()

        if existing_vote:
            # 如果用户已经投过票，检查是否是取消投票
            if existing_vote.vote == vote_value:
                # 相同票值，删除投票（取消）
                await self.session.delete(existing_vote)
            else:
                # 不同票值，更新投票
                existing_vote.vote = vote_value
                self.session.add(existing_vote)
        else:
            # 创建新投票
            new_vote = TagVote(
                user_id=user_id,
                thread_id=db_thread_id,
                tag_id=tag_id,
                vote=vote_value
            )
            self.session.add(new_vote)
        
        await self.session.flush()
        
        # 更新投票摘要
        await self._update_thread_vote_summary(thread_id)
        
        await self.session.commit()

    async def get_tag_vote_stats(self, thread_id: int) -> dict:
        """
        获取一个帖子的标签投票统计。
        直接从帖子的摘要字段读取。
        """
        statement = select(Thread).where(Thread.thread_id == thread_id).options(selectinload(Thread.tags))
        result = await self.session.execute(statement)
        db_thread = result.first()

        if not db_thread or not db_thread.tag_votes_summary:
            return {}

        # 将tag_id键转换为tag_name键
        summary = db_thread.tag_votes_summary
        tag_map = {tag.id: tag.name for tag in db_thread.tags}
        
        stats = {}
        for tag_id, data in summary.items():
            tag_name = tag_map.get(int(tag_id))
            if tag_name:
                stats[tag_name] = data
        
        return stats