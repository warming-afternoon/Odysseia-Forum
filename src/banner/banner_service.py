"""Banner申请和管理服务"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Tuple
from sqlmodel import select, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from shared.models.banner_application import (
    BannerApplication,
    BannerCarousel,
    BannerWaitlist,
    ApplicationStatus,
)
from shared.models.thread import Thread

logger = logging.getLogger(__name__)


class BannerService:
    """Banner申请和轮播管理服务"""

    # 全频道最多3个banner
    GLOBAL_MAX_BANNERS = 3
    # 每个频道最多5个banner
    CHANNEL_MAX_BANNERS = 5
    # Banner展示时长：3天
    BANNER_DURATION_DAYS = 3

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_application(
        self,
        thread_id: int,
        channel_id: int,
        applicant_id: int,
        cover_image_url: str,
        target_scope: str,
    ) -> BannerApplication:
        """创建Banner申请"""
        application = BannerApplication(
            thread_id=thread_id,
            channel_id=channel_id,
            applicant_id=applicant_id,
            cover_image_url=cover_image_url,
            target_scope=target_scope,
            status=ApplicationStatus.PENDING.value,
            applied_at=datetime.now(timezone.utc),
        )
        self.session.add(application)
        await self.session.commit()
        await self.session.refresh(application)
        return application

    async def approve_application(
        self, application_id: int, reviewer_id: int
    ) -> Tuple[BannerApplication, bool]:
        """
        批准申请并将banner加入轮播或等待列表
        
        Returns:
            Tuple[BannerApplication, bool]: (申请记录, 是否直接进入轮播)
        """
        # 获取申请
        result = await self.session.execute(
            select(BannerApplication).where(BannerApplication.id == application_id)
        )
        application = result.scalar_one_or_none()
        if not application:
            raise ValueError("申请不存在")

        # 获取帖子信息
        thread_result = await self.session.execute(
            select(Thread).where(Thread.thread_id == application.thread_id)
        )
        thread = thread_result.scalar_one_or_none()
        if not thread:
            raise ValueError("帖子不存在")

        # 更新申请状态
        application.status = ApplicationStatus.APPROVED.value
        application.reviewed_at = datetime.now(timezone.utc)
        application.reviewer_id = reviewer_id

        # 判断是全频道还是特定频道
        is_global = application.target_scope == "global"
        channel_id_for_carousel = None if is_global else int(application.target_scope)

        # 检查当前轮播列表是否已满
        max_banners = self.GLOBAL_MAX_BANNERS if is_global else self.CHANNEL_MAX_BANNERS
        current_count = await self._get_active_banner_count(channel_id_for_carousel)

        if current_count < max_banners:
            # 直接加入轮播列表
            await self._add_to_carousel(
                thread_id=application.thread_id,
                channel_id=channel_id_for_carousel,
                cover_image_url=application.cover_image_url,
                title=thread.title,
            )
            entered_carousel = True
        else:
            # 加入等待列表
            await self._add_to_waitlist(
                thread_id=application.thread_id,
                channel_id=channel_id_for_carousel,
                cover_image_url=application.cover_image_url,
                title=thread.title,
            )
            entered_carousel = False

        await self.session.commit()
        await self.session.refresh(application)
        return application, entered_carousel

    async def reject_application(
        self, application_id: int, reviewer_id: int, reason: str
    ) -> BannerApplication:
        """拒绝申请"""
        result = await self.session.execute(
            select(BannerApplication).where(BannerApplication.id == application_id)
        )
        application = result.scalar_one_or_none()
        if not application:
            raise ValueError("申请不存在")

        application.status = ApplicationStatus.REJECTED.value
        application.reviewed_at = datetime.now(timezone.utc)
        application.reviewer_id = reviewer_id
        application.reject_reason = reason

        await self.session.commit()
        await self.session.refresh(application)
        return application

    async def _add_to_carousel(
        self,
        thread_id: int,
        channel_id: Optional[int],
        cover_image_url: str,
        title: str,
    ):
        """添加到轮播列表"""
        start_time = datetime.now(timezone.utc)
        end_time = start_time + timedelta(days=self.BANNER_DURATION_DAYS)

        # 获取当前最大position
        result = await self.session.execute(
            select(BannerCarousel.position)
            .where(
                and_(
                    BannerCarousel.channel_id == channel_id,
                    BannerCarousel.end_time > start_time,
                )
            )
            .order_by(desc(BannerCarousel.position))
            .limit(1)
        )
        max_position = result.scalar_one_or_none()
        new_position = (max_position + 1) if max_position is not None else 0

        carousel_item = BannerCarousel(
            thread_id=thread_id,
            channel_id=channel_id,
            cover_image_url=cover_image_url,
            title=title,
            start_time=start_time,
            end_time=end_time,
            position=new_position,
        )
        self.session.add(carousel_item)

    async def _add_to_waitlist(
        self,
        thread_id: int,
        channel_id: Optional[int],
        cover_image_url: str,
        title: str,
    ):
        """添加到等待列表"""
        # 获取当前最大position
        result = await self.session.execute(
            select(BannerWaitlist.position)
            .where(BannerWaitlist.channel_id == channel_id)
            .order_by(desc(BannerWaitlist.position))
            .limit(1)
        )
        max_position = result.scalar_one_or_none()
        new_position = (max_position + 1) if max_position is not None else 0

        waitlist_item = BannerWaitlist(
            thread_id=thread_id,
            channel_id=channel_id,
            cover_image_url=cover_image_url,
            title=title,
            queued_at=datetime.now(timezone.utc),
            position=new_position,
        )
        self.session.add(waitlist_item)

    async def _get_active_banner_count(self, channel_id: Optional[int]) -> int:
        """获取当前活跃的banner数量"""
        now = datetime.now(timezone.utc)
        result = await self.session.execute(
            select(BannerCarousel)
            .where(
                and_(
                    BannerCarousel.channel_id == channel_id,
                    BannerCarousel.end_time > now,
                )
            )
        )
        return len(result.scalars().all())

    async def cleanup_expired_banners(self) -> int:
        """清理过期的banner并从等待列表补充"""
        now = datetime.now(timezone.utc)
        
        # 查找所有过期的banner
        result = await self.session.execute(
            select(BannerCarousel).where(BannerCarousel.end_time <= now)
        )
        expired_banners = result.scalars().all()

        cleaned_count = 0
        for banner in expired_banners:
            # 删除过期banner
            await self.session.delete(banner)
            cleaned_count += 1

            # 从等待列表中取出下一个
            await self._promote_from_waitlist(banner.channel_id)

        await self.session.commit()
        return cleaned_count

    async def _promote_from_waitlist(self, channel_id: Optional[int]):
        """从等待列表提升一个banner到轮播列表"""
        result = await self.session.execute(
            select(BannerWaitlist)
            .where(BannerWaitlist.channel_id == channel_id)
            .order_by(BannerWaitlist.position)
            .limit(1)
        )
        waitlist_item = result.scalar_one_or_none()

        if waitlist_item:
            # 添加到轮播列表
            await self._add_to_carousel(
                thread_id=waitlist_item.thread_id,
                channel_id=waitlist_item.channel_id,
                cover_image_url=waitlist_item.cover_image_url,
                title=waitlist_item.title,
            )
            # 从等待列表删除
            await self.session.delete(waitlist_item)

    async def get_active_banners(
        self, channel_id: Optional[int] = None
    ) -> List[BannerCarousel]:
        """获取活跃的banner列表"""
        now = datetime.now(timezone.utc)
        
        if channel_id is None:
            # 获取全频道的banner
            result = await self.session.execute(
                select(BannerCarousel)
                .where(
                    and_(
                        BannerCarousel.channel_id.is_(None),
                        BannerCarousel.end_time > now,
                    )
                )
                .order_by(BannerCarousel.position)
            )
        else:
            # 获取特定频道+全频道的banner，合并后最多8个
            # 先获取频道特定的（最多5个）
            channel_result = await self.session.execute(
                select(BannerCarousel)
                .where(
                    and_(
                        BannerCarousel.channel_id == channel_id,
                        BannerCarousel.end_time > now,
                    )
                )
                .order_by(BannerCarousel.position)
                .limit(self.CHANNEL_MAX_BANNERS)
            )
            channel_banners = list(channel_result.scalars().all())
            
            # 再获取全频道的（最多3个）
            global_result = await self.session.execute(
                select(BannerCarousel)
                .where(
                    and_(
                        BannerCarousel.channel_id.is_(None),
                        BannerCarousel.end_time > now,
                    )
                )
                .order_by(BannerCarousel.position)
                .limit(self.GLOBAL_MAX_BANNERS)
            )
            global_banners = list(global_result.scalars().all())
            
            # 合并并返回
            return channel_banners + global_banners

        return list(result.scalars().all())

    async def update_review_message_info(
        self, application_id: int, review_message_id: int, review_thread_id: int
    ):
        """更新审核记录消息信息"""
        result = await self.session.execute(
            select(BannerApplication).where(BannerApplication.id == application_id)
        )
        application = result.scalar_one_or_none()
        if application:
            application.review_message_id = review_message_id
            application.review_thread_id = review_thread_id
            await self.session.commit()