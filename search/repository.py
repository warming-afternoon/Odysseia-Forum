from typing import List, Optional, Sequence
from sqlmodel import select, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.models.thread import Thread
from shared.models.tag import Tag
from shared.models.user_search_preferences import UserSearchPreferences
from search.models.qo.thread_search import ThreadSearchQuery

class SearchRepository:
    """封装与搜索相关的数据库操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _apply_search_filters(self, query: ThreadSearchQuery, statement):
        """将搜索查询对象中的过滤条件应用到查询语句上"""
        if query.channel_ids:
            statement = statement.where(Thread.channel_id.in_(query.channel_ids))
        
        if query.include_authors:
            statement = statement.where(Thread.author_id.in_(query.include_authors))
        if query.exclude_authors:
            statement = statement.where(Thread.author_id.notin_(query.exclude_authors))

        if query.after_date:
            statement = statement.where(Thread.created_at >= query.after_date)
        if query.before_date:
            statement = statement.where(Thread.created_at <= query.before_date)

        if query.include_tags:
            if query.tag_logic == 'and':
                for tag_name in query.include_tags:
                    statement = statement.where(Thread.tags.any(Tag.name == tag_name))
            else: # or
                statement = statement.where(Thread.tags.any(Tag.name.in_(query.include_tags)))
        
        if query.exclude_tags:
            statement = statement.where(~Thread.tags.any(Tag.name.in_(query.exclude_tags)))

        if query.keywords:
            keyword_clauses = []
            for keyword in query.keywords:
                keyword_clauses.append(Thread.title.ilike(f"%{keyword}%"))
                keyword_clauses.append(Thread.first_message_excerpt.ilike(f"%{keyword}%"))
            statement = statement.where(or_(*keyword_clauses))

        return statement

    async def count_threads(self, query: ThreadSearchQuery) -> int:
        """根据搜索条件统计帖子总数"""
        statement = select(func.count(Thread.id))
        statement = self._apply_search_filters(query, statement)
        
        result = await self.session.exec(statement)
        return result.one()

    async def search_threads(self, query: ThreadSearchQuery, offset: int, limit: int) -> Sequence[Thread]:
        """根据搜索条件搜索帖子并分页"""
        statement = select(Thread).options(selectinload(Thread.tags))
        statement = self._apply_search_filters(query, statement)

        # 排序
        if query.sort_method == 'created_time':
            order_by = Thread.created_at.desc() if query.sort_order == 'desc' else Thread.created_at.asc()
        elif query.sort_method == 'active_time':
            order_by = Thread.last_active_at.desc() if query.sort_order == 'desc' else Thread.last_active_at.asc()
        elif query.sort_method == 'reaction_count':
            order_by = Thread.reaction_count.desc() if query.sort_order == 'desc' else Thread.reaction_count.asc()
        else: # 默认为 'comprehensive' 或其他情况，暂时按活跃时间排序
            order_by = Thread.last_active_at.desc() if query.sort_order == 'desc' else Thread.last_active_at.asc()

        statement = statement.order_by(order_by).offset(offset).limit(limit)
        
        result = await self.session.exec(statement)
        return result.unique().all()

    async def get_user_preferences(self, user_id: int) -> Optional[UserSearchPreferences]:
        """获取用户的搜索偏好设置。"""
        result = await self.session.get(UserSearchPreferences, user_id)
        return result

    async def save_user_preferences(self, user_id: int, prefs_data: dict) -> UserSearchPreferences:
        """创建或更新用户的搜索偏好设置。"""
        prefs = await self.get_user_preferences(user_id)
        if prefs:
            for key, value in prefs_data.items():
                setattr(prefs, key, value)
        else:
            prefs = UserSearchPreferences(user_id=user_id, **prefs_data)
        
        self.session.add(prefs)
        await self.session.commit()
        await self.session.refresh(prefs)
        return prefs

    async def get_tags_for_author(self, author_id: int) -> Sequence[Tag]:
        """获取指定作者使用过的所有标签。"""
        statement = (
            select(Tag)
            .join(Thread.tags)
            .where(Thread.author_id == author_id)
            .distinct()
        )
        result = await self.session.exec(statement)
        return result.all()