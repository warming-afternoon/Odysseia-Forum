import logging
from typing import List, Sequence
from datetime import datetime
from shared.models.tag_vote import TagVote
from sqlmodel import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, attributes
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from shared.models.thread import Thread
from shared.models.tag import Tag

logger = logging.getLogger(__name__)

class TagSystemRepository:
   """封装与标签系统相关的数据库操作。"""

   def __init__(self, session: AsyncSession):
       self.session = session

   async def get_or_create_tags(self, tags_data: dict[int, str]) -> List[Tag]:
       """
       根据标签ID和名称的字典，获取或创建标签对象。
       此操作是幂等的，并能安全地处理并发请求。
       """
       if not tags_data:
           return []

       # 步骤 1: 使用 INSERT ... ON CONFLICT DO NOTHING 批量插入所有可能的标签。
       # 这确保了所有标签在数据库中都存在，并且是原子操作，避免了并发冲突。
       insert_stmt = sqlite_insert(Tag).values(
           [{"id": id, "name": name} for id, name in tags_data.items()]
       )
       do_nothing_stmt = insert_stmt.on_conflict_do_nothing(
           index_elements=['id']
       )
       await self.session.execute(do_nothing_stmt)

       # 步骤 2: 更新可能已更改名称的标签。
       # (这一步在实践中很少发生，因为Discord标签ID是唯一的，但为了健壮性而保留)
       tag_ids = list(tags_data.keys())
       statement = select(Tag).where(Tag.id.in_(tag_ids))
       result = await self.session.execute(statement)
       existing_tags_map = {tag.id: tag for tag in result.scalars().all()}

       updated = False
       for tag_id, tag in existing_tags_map.items():
           if tags_data[tag_id] != tag.name:
               tag.name = tags_data[tag_id]
               self.session.add(tag)
               updated = True
       
       if updated:
           await self.session.flush()

       # 步骤 3: 返回所有相关的标签对象。
       # 此时，我们知道所有需要的标签都已在数据库中，并且名称是最新的。
       # 重新查询以获取完整的对象列表。
       final_statement = select(Tag).where(Tag.id.in_(tag_ids))
       final_result = await self.session.execute(final_statement)
       return final_result.scalars().all()

   async def add_or_update_thread_with_tags(self, thread_data: dict, tags_data: dict[int, str]):
       """
       原子性地添加或更新一个帖子及其标签。
       """
       # 查找现有帖子
       statement = select(Thread).where(Thread.thread_id == thread_data['thread_id']).options(selectinload(Thread.tags))
       result = await self.session.execute(statement)
       db_thread = result.scalars().first()

       # 获取或创建所有相关标签
       tags = await self.get_or_create_tags(tags_data)

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
       return result.scalars().all()

   async def delete_thread_index(self, thread_id: int):
       """删除帖子的所有相关索引数据"""
       statement = select(Thread).where(Thread.thread_id == thread_id)
       result = await self.session.execute(statement)
       db_thread = result.scalars().first()
       if db_thread:
           await self.session.delete(db_thread)
           await self.session.commit()

   async def update_thread_activity(self, thread_id: int, last_active_at: datetime, reply_count: int):
       """仅更新帖子的活跃时间和回复数"""
       statement = select(Thread).where(Thread.thread_id == thread_id)
       result = await self.session.execute(statement)
       db_thread = result.scalars().first()

       if db_thread:
           db_thread.last_active_at = last_active_at
           db_thread.reply_count = reply_count
           self.session.add(db_thread)
           await self.session.commit()

   async def update_thread_reaction_count(self, thread_id: int, reaction_count: int):
       """仅更新帖子的反应数"""
       statement = select(Thread).where(Thread.thread_id == thread_id)
       result = await self.session.execute(statement)
       db_thread = result.scalars().first()

       if db_thread:
           db_thread.reaction_count = reaction_count
           self.session.add(db_thread)
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
       return result.scalars().all()
   
   async def get_tags_for_channels(self, channel_ids: List[int]) -> Sequence[Tag]:
       """获取指定频道列表内的所有唯一标签"""
       statement = (
           select(Tag)
           .join(Thread, Tag.threads)
           .where(Thread.channel_id.in_(channel_ids))
           .distinct()
       )
       result = await self.session.execute(statement)
       return result.scalars().all()
   
   async def get_all_tags(self) -> Sequence[Tag]:
       """获取数据库中所有的标签。"""
       statement = select(Tag)
       result = await self.session.execute(statement)
       return result.scalars().all()

   async def update_tag_name(self, tag_id: int, new_name: str):
       """更新指定ID的标签的名称。"""
       statement = select(Tag).where(Tag.id == tag_id)
       result = await self.session.execute(statement)
       tag = result.scalars().first()
       if tag:
           tag.name = new_name
           self.session.add(tag)
           await self.session.commit()

   async def record_tag_vote(self, user_id: int, thread_id: int, tag_id: int, vote_value: int, tag_map: dict[int, str]) -> dict:
       """
       记录一次标签投票，并更新帖子的投票摘要。
       """
       # 查找帖子对象
       thread_statement = select(Thread).where(Thread.thread_id == thread_id)
       thread_result = await self.session.execute(thread_statement)
       db_thread = thread_result.scalars().first()
       if not db_thread:
           logger.warning(f"record_tag_vote: 未找到 thread_id={thread_id} 的帖子。")
           return {}

       # 初始化摘要字典
       if db_thread.tag_votes_summary is None:
           db_thread.tag_votes_summary = {}
       
       summary = db_thread.tag_votes_summary.copy()
       tag_id_str = str(tag_id)

       # 查找现有投票
       statement = select(TagVote).where(
           TagVote.user_id == user_id,
           TagVote.thread_id == db_thread.id,
           TagVote.tag_id == tag_id
       )
       result = await self.session.execute(statement)
       existing_vote = result.scalars().first()

       # 在内存中更新摘要
       if tag_id_str not in summary:
           summary[tag_id_str] = {"upvotes": 0, "downvotes": 0, "score": 0}

       if existing_vote:
           previous_vote = existing_vote.vote
           if previous_vote == vote_value:
               summary[tag_id_str]["upvotes" if previous_vote == 1 else "downvotes"] -= 1
               summary[tag_id_str]["score"] -= previous_vote
               await self.session.delete(existing_vote)
           else:
               summary[tag_id_str]["upvotes" if previous_vote == 1 else "downvotes"] -= 1
               summary[tag_id_str]["upvotes" if vote_value == 1 else "downvotes"] += 1
               summary[tag_id_str]["score"] -= previous_vote
               summary[tag_id_str]["score"] += vote_value
               existing_vote.vote = vote_value
               self.session.add(existing_vote)
       else:
           summary[tag_id_str]["upvotes" if vote_value == 1 else "downvotes"] += 1
           summary[tag_id_str]["score"] += vote_value
           new_vote = TagVote(
               user_id=user_id,
               thread_id=db_thread.id,
               tag_id=tag_id,
               vote=vote_value
           )
           self.session.add(new_vote)
       
       db_thread.tag_votes_summary = summary
       attributes.flag_modified(db_thread, "tag_votes_summary")
       self.session.add(db_thread)

       await self.session.commit()
       
       # 使用传入的 tag_map 来构建完整的统计数据
       stats = {}
       for sid_str, data in summary.items():
           sid_int = int(sid_str)
           tag_name = tag_map.get(sid_int)
           if tag_name:
               stats[tag_name] = data
       
       return stats

   async def get_tag_vote_stats(self, thread_id: int, tag_map: dict[int, str]) -> dict:
       """
       获取一个帖子的标签投票统计。
       直接从帖子的摘要字段读取
       """
       statement = select(Thread).where(Thread.thread_id == thread_id)
       result = await self.session.execute(statement)
       db_thread = result.scalars().first()

       if not db_thread or not db_thread.tag_votes_summary:
           return {}

       # 使用传入的、可靠的 tag_map 将 tag_id 键转换为 tag_name 键
       summary = db_thread.tag_votes_summary
       
       stats = {}
       for tag_id, data in summary.items():
           tag_name = tag_map.get(int(tag_id))
           if tag_name:
               stats[tag_name] = data
       
       return stats