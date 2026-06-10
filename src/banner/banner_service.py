"""Banner申请和管理服务"""

import logging
from datetime import datetime, timezone
from typing import List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession

from banner.dto.application_result import ApplicationResult
from banner.dto.delete_banner_result import DeleteBannerResult
from core.banner_application_repository import BannerApplicationRepository
from core.banner_carousel_repository import BannerCarouselRepository
from core.banner_waitlist_repository import BannerWaitlistRepository
from core.thread_repository import ThreadRepository
from dto.thread_dto import ThreadDTO
from models import (
    BannerApplication,
    BannerCarousel,
)
from shared.enum import ApplicationStatus

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
        self.app_repo = BannerApplicationRepository(session)
        self.carousel_repo = BannerCarouselRepository(session)
        self.waitlist_repo = BannerWaitlistRepository(session)

    async def validate_application_request(
        self,
        thread_id: int,
        applicant_id: int,
        cover_image_url: str,
        target_scope: Optional[str] = None,
    ) -> ApplicationResult:
        """
        验证Banner申请请求（不创建申请）。

        用于在用户选择展示范围前进行预验证。
        """
        # 验证封面图URL格式
        cover_url = cover_image_url.strip()
        if not cover_url.startswith(("http://", "https://")):
            return ApplicationResult(
                success=False,
                message="封面图链接必须是有效的URL（以http://或https://开头）",
            )

        # 验证展示范围（如果提供）
        if target_scope is not None:
            scope = target_scope.strip()
            if scope != "global" and not scope.isdigit():
                return ApplicationResult(
                    success=False, message="展示范围必须是'global'或有效的频道ID"
                )

        # 验证帖子存在（通过 ThreadRepository）
        

        repo = ThreadRepository(self.session)
        thread = await repo.get_thread_with_tags(thread_id)

        if not thread:
            return ApplicationResult(
                success=False,
                message="该帖子未被索引，无法申请Banner。请确保帖子ID正确。",
            )

        # 验证申请人是帖子作者
        if thread.author_id != applicant_id:
            return ApplicationResult(
                success=False, message="只能为自己的帖子申请Banner"
            )

        return ApplicationResult(
            success=True,
            message="验证通过",
            thread=ThreadDTO.from_orm(thread),
        )

    async def validate_and_create_application(
        self,
        thread_id: int,
        applicant_id: int,
        cover_image_url: str,
        target_scope: str,
    ) -> ApplicationResult:
        """
        验证并创建Banner申请。

        完整的申请流程，包括验证帖子存在、申请人是作者、
        封面图URL格式、展示范围，然后创建申请记录。
        """
        validation = await self.validate_application_request(
            thread_id=thread_id,
            applicant_id=applicant_id,
            cover_image_url=cover_image_url,
            target_scope=target_scope,
        )

        if not validation.success:
            return validation

        thread = validation.thread
        if thread is None:
            return ApplicationResult(
                success=False, message="验证通过但帖子数据丢失，请重试"
            )

        cover_url = cover_image_url.strip()
        scope = target_scope.strip()

        application = await self.app_repo.create(
            thread_id=thread_id,
            channel_id=thread.channel_id,
            applicant_id=applicant_id,
            cover_image_url=cover_url,
            target_scope=scope,
        )
        await self.session.commit()
        await self.session.refresh(application)

        logger.info(
            f"用户 {applicant_id} 提交了Banner申请，帖子ID: {thread_id}，范围: {scope}"
        )

        return ApplicationResult(
            success=True,
            message="Banner申请已提交，等待审核",
            application=application,
            thread=thread,
        )

    async def approve_application(
        self, application_id: int, reviewer_id: int
    ) -> Tuple[BannerApplication, bool]:
        """
        批准申请并将banner加入轮播或等待列表。

        Returns:
            Tuple[BannerApplication, bool]: (申请记录, 是否直接进入轮播)
        """
        from core.thread_repository import ThreadRepository

        # 获取申请
        application = await self.app_repo.get_by_id(application_id)
        if not application:
            raise ValueError("申请不存在")

        # 获取帖子标题
        repo = ThreadRepository(self.session)
        thread_obj = await repo.get_thread_with_tags(application.thread_id)
        if not thread_obj:
            raise ValueError("帖子不存在")
        thread_title = thread_obj.title

        # 更新申请状态
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        application.status = ApplicationStatus.APPROVED.value
        application.reviewed_at = now
        application.reviewer_id = reviewer_id

        # 判断是全频道还是特定频道
        is_global = application.target_scope == "global"
        channel_id = None if is_global else int(application.target_scope)

        # 检查当前轮播列表是否已满
        max_banners = self.GLOBAL_MAX_BANNERS if is_global else self.CHANNEL_MAX_BANNERS
        current_count = await self.carousel_repo.get_count(channel_id)

        if current_count < max_banners:
            await self.carousel_repo.add(
                thread_id=application.thread_id,
                channel_id=channel_id,
                cover_image_url=application.cover_image_url,
                title=thread_title,
                duration_days=self.BANNER_DURATION_DAYS,
            )
            entered_carousel = True
        else:
            await self.waitlist_repo.add(
                thread_id=application.thread_id,
                channel_id=channel_id,
                cover_image_url=application.cover_image_url,
                title=thread_title,
            )
            entered_carousel = False

        await self.session.commit()
        await self.session.refresh(application)
        return application, entered_carousel

    async def reject_application(
        self, application_id: int, reviewer_id: int, reason: str
    ) -> BannerApplication:
        """拒绝申请。"""
        application = await self.app_repo.get_by_id(application_id)
        if not application:
            raise ValueError("申请不存在")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        application.status = ApplicationStatus.REJECTED.value
        application.reviewed_at = now
        application.reviewer_id = reviewer_id
        application.reject_reason = reason

        await self.session.commit()
        await self.session.refresh(application)
        return application

    async def cleanup_expired_banners(self) -> int:
        """清理过期的banner并从等待列表补充。"""
        expired = await self.carousel_repo.get_expired()

        cleaned_count = 0
        for banner in expired:
            channel_id = banner.channel_id
            await self.carousel_repo.delete(banner)
            cleaned_count += 1

            # 从等待队列晋升
            waitlist_item = await self.waitlist_repo.pop(channel_id)
            if waitlist_item:
                await self.carousel_repo.add(
                    thread_id=waitlist_item.thread_id,
                    channel_id=waitlist_item.channel_id,
                    cover_image_url=waitlist_item.cover_image_url,
                    title=waitlist_item.title,
                    duration_days=self.BANNER_DURATION_DAYS,
                )

        await self.session.commit()
        return cleaned_count

    async def delete_banner_by_thread(
        self, thread_id: int
    ) -> DeleteBannerResult:
        """
        根据 thread_id 从轮播或等待列表中删除 Banner。

        如果 Banner 在轮播中，删除后会尝试从等待队列晋升替补。
        """
        carousel_items = await self.carousel_repo.get_by_thread(thread_id)
        waitlist_items = await self.waitlist_repo.get_by_thread(thread_id)

        # 无匹配
        if not carousel_items and not waitlist_items:
            return DeleteBannerResult(
                success=False,
                message="未找到该帖子ID对应的Banner记录，请在轮播列表或等待列表中确认。",
            )

        # 多条匹配（数据一致性的安全网）
        if len(carousel_items) + len(waitlist_items) > 1:
            parts = []
            for item in carousel_items:
                scope = "全频道" if item.channel_id is None else f"频道 {item.channel_id}"
                parts.append(f"  • 轮播列表: {item.title[:40]} (范围: {scope})")
            for item in waitlist_items:
                scope = "全频道" if item.channel_id is None else f"频道 {item.channel_id}"
                parts.append(f"  • 等待列表: {item.title[:40]} (范围: {scope})")
            return DeleteBannerResult(
                success=False,
                message=f"该帖子ID对应多条Banner记录，删除时存在歧义：\n" + "\n".join(parts),
            )

        # 在轮播中找到
        if carousel_items:
            banner = carousel_items[0]
            channel_id = banner.channel_id
            title = banner.title
            scope_label = "全频道" if channel_id is None else f"频道 {channel_id}"

            has_waiting = await self.waitlist_repo.has_item(channel_id)

            await self.carousel_repo.delete(banner)

            if has_waiting:
                waitlist_item = await self.waitlist_repo.pop(channel_id)
                if waitlist_item:
                    await self.carousel_repo.add(
                        thread_id=waitlist_item.thread_id,
                        channel_id=waitlist_item.channel_id,
                        cover_image_url=waitlist_item.cover_image_url,
                        title=waitlist_item.title,
                        duration_days=self.BANNER_DURATION_DAYS,
                    )

            await self.session.commit()

            return DeleteBannerResult(
                success=True,
                message=f"已从轮播列表中删除Banner「{title}」",
                deleted_from="carousel",
                thread_id=thread_id,
                banner_title=title,
                scope_label=scope_label,
                promoted_from_waitlist=has_waiting,
            )

        # 在等待列表中找到
        if waitlist_items:
            banner = waitlist_items[0]
            channel_id = banner.channel_id
            title = banner.title
            scope_label = "全频道" if channel_id is None else f"频道 {channel_id}"

            await self.waitlist_repo.delete(banner)
            await self.session.commit()

            return DeleteBannerResult(
                success=True,
                message=f"已从等待列表中删除Banner「{title}」",
                deleted_from="waitlist",
                thread_id=thread_id,
                banner_title=title,
                scope_label=scope_label,
                promoted_from_waitlist=False,
            )

        # 不应到达此处
        return DeleteBannerResult(
            success=False,
            message="删除Banner时发生未预期的错误",
        )

    async def get_active_banners(
        self, channel_id: Optional[int] = None
    ) -> List[BannerCarousel]:
        """获取活跃的banner列表。"""
        return await self.carousel_repo.get_active(channel_id=channel_id)

    async def update_review_message_info(
        self, application_id: int, review_message_id: int, review_thread_id: int
    ):
        """更新审核记录消息信息。"""
        updated = await self.app_repo.update_review_message_info(
            application_id, review_message_id, review_thread_id
        )
        if updated:
            await self.session.commit()

    async def get_application_by_review_message(
        self, review_message_id: int
    ) -> Optional[BannerApplication]:
        """通过审核消息ID获取申请记录。"""
        return await self.app_repo.get_by_review_message(review_message_id)
