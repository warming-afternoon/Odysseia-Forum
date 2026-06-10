from datetime import datetime, timedelta
from typing import List, Optional, cast

from sqlalchemy import ColumnElement
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import and_, desc, select

from models import BannerCarousel


class BannerCarouselRepository:
    """banner_carousel 表的数据库操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_active(
        self, channel_id: Optional[int] = None
    ) -> List[BannerCarousel]:
        """获取当前有效的轮播项（end_time > now）。"""
        now = datetime.now().replace(microsecond=0)
        channel_id_col = cast(ColumnElement, BannerCarousel.channel_id)
        end_time_col = cast(ColumnElement, BannerCarousel.end_time)
        position_col = cast(ColumnElement, BannerCarousel.position)

        if channel_id is None:
            result = await self.session.execute(
                select(BannerCarousel)
                .where(and_(channel_id_col.is_(None), end_time_col > now))
                .order_by(position_col)
            )
            return list(result.scalars().all())

        # 频道专属
        channel_result = await self.session.execute(
            select(BannerCarousel)
            .where(and_(channel_id_col == channel_id, end_time_col > now))
            .order_by(position_col)
            .limit(5)
        )
        channel_banners = list(channel_result.scalars().all())

        # 全频道
        global_result = await self.session.execute(
            select(BannerCarousel)
            .where(and_(channel_id_col.is_(None), end_time_col > now))
            .order_by(position_col)
            .limit(3)
        )
        global_banners = list(global_result.scalars().all())

        return channel_banners + global_banners

    async def get_count(self, channel_id: Optional[int]) -> int:
        """统计指定频道的当前有效轮播数。"""
        now = datetime.now().replace(microsecond=0)
        channel_id_col = cast(ColumnElement, BannerCarousel.channel_id)
        end_time_col = cast(ColumnElement, BannerCarousel.end_time)

        result = await self.session.execute(
            select(BannerCarousel).where(
                and_(channel_id_col == channel_id, end_time_col > now)
            )
        )
        return len(result.scalars().all())

    async def get_expired(self) -> List[BannerCarousel]:
        """获取所有已过期的轮播项。"""
        now = datetime.now().replace(microsecond=0)
        end_time_col = cast(ColumnElement, BannerCarousel.end_time)

        result = await self.session.execute(
            select(BannerCarousel).where(end_time_col <= now)
        )
        return list(result.scalars().all())

    async def get_by_thread(self, thread_id: int) -> List[BannerCarousel]:
        """按帖子 ID 查询轮播项。"""
        result = await self.session.execute(
            select(BannerCarousel).where(BannerCarousel.thread_id == thread_id)
        )
        return list(result.scalars().all())

    async def add(
        self,
        thread_id: int,
        channel_id: Optional[int],
        cover_image_url: str,
        title: str,
        duration_days: int,
    ) -> None:
        """插入一条轮播记录（含 position 计算）。"""
        start_time = datetime.now().replace(microsecond=0)
        end_time = start_time + timedelta(days=duration_days)
        channel_id_col = cast(ColumnElement, BannerCarousel.channel_id)
        end_time_col = cast(ColumnElement, BannerCarousel.end_time)
        position_col = cast(ColumnElement, BannerCarousel.position)

        result = await self.session.execute(
            select(position_col)
            .where(and_(channel_id_col == channel_id, end_time_col > start_time))
            .order_by(desc(position_col))
            .limit(1)
        )
        max_position = result.scalar_one_or_none()
        new_position = (max_position + 1) if max_position is not None else 0

        item = BannerCarousel(
            thread_id=thread_id,
            channel_id=channel_id,
            cover_image_url=cover_image_url,
            title=title,
            start_time=start_time,
            end_time=end_time,
            position=new_position,
        )
        self.session.add(item)

    async def delete(self, item: BannerCarousel) -> None:
        """从会话中移除一条轮播记录。"""
        await self.session.delete(item)
