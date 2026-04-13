from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, joinedload
from sqlmodel import select, case
from models import Thread, Tag, ThreadTagLink
from dto.preferences.user_search_preferences import UserSearchPreferencesDTO

class DiscoveryRepository:
    """提供发现页所需的底层数据拉取功能"""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _apply_preferences_filter(self, stmt, prefs: Optional[UserSearchPreferencesDTO]):
        """应用用户偏好到查询构建器"""
        if not prefs:
            return stmt

        if prefs.exclude_authors:
            stmt = stmt.where(Thread.author_id.notin_(prefs.exclude_authors))  # type: ignore
            
        if prefs.preferred_channels:
            stmt = stmt.where(Thread.channel_id.in_(prefs.preferred_channels))  # type: ignore

        if prefs.exclude_tags:
            subquery = (
                select(ThreadTagLink.thread_id)
                .join(Tag, Tag.id == ThreadTagLink.tag_id)  # type: ignore
                .where(Tag.name.in_(prefs.exclude_tags))  # type: ignore
            )
            stmt = stmt.where(Thread.id.notin_(subquery))  # type: ignore

        return stmt

    async def get_latest_threads(self, limit: int, prefs: Optional[UserSearchPreferencesDTO]) -> List[Thread]:
        """拉取最新的帖子列表"""
        stmt = select(Thread).where(Thread.not_found_count == 0, Thread.show_flag.is_(True))  # type: ignore
        
        stmt = self._apply_preferences_filter(stmt, prefs)
        
        stmt = stmt.options(selectinload(Thread.tags), joinedload(Thread.author))  # type: ignore
        stmt = stmt.order_by(Thread.created_at.desc()).limit(limit)  # type: ignore
        
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())

    async def get_threads_by_ids_ordered(self, thread_ids: List[int], prefs: Optional[UserSearchPreferencesDTO]) -> List[Thread]:
        """拉取指定ID列表的帖子并依靠传入的列表保持顺序不变"""
        if not thread_ids:
            return []

        stmt = select(Thread).where(
            Thread.thread_id.in_(thread_ids),  # type: ignore
            Thread.not_found_count == 0,
            Thread.show_flag.is_(True)  # type: ignore
        )
        
        stmt = self._apply_preferences_filter(stmt, prefs)
        
        stmt = stmt.options(selectinload(Thread.tags), joinedload(Thread.author))  # type: ignore
        
        # 构建按照目标ID数组排列的排序条件
        order_case = case(
            {tid: index for index, tid in enumerate(thread_ids)},
            value=Thread.thread_id
        )
        stmt = stmt.order_by(order_case)
        
        result = await self.session.execute(stmt)
        return list(result.scalars().unique().all())