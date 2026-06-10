"""Banner申请和管理服务"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Optional, Tuple

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload
from sqlmodel import and_, desc, select

from banner.dto.delete_banner_result import DeleteBannerResult
from models import (
    BannerApplication,
    BannerCarousel,
    BannerWaitlist,
    Thread,
)
from dto.thread_dto import ThreadDTO
from shared.enum import ApplicationStatus

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


@dataclass
class ApplicationResult:
    """申请结果"""

    success: bool
    message: str
    application: Optional[BannerApplication] = None
    thread: Optional[ThreadDTO] = None


async def send_review_message(
    bot: "MyBot",
    session_factory: async_sessionmaker,
    application: BannerApplication,
    config: dict,
    guild_id: Optional[int] = None,
) -> bool:
    """
    发送审核消息到指定的审核子区

    Args:
        bot: Discord bot 实例
        session_factory: 数据库会话工厂
        application: Banner申请记录
        config: Banner配置（包含review_thread_id等）
        guild_id: 服务器ID（用于构建帖子链接）

    Returns:
        bool: 是否发送成功
    """
    import discord

    from banner.views.review_view import ReviewView

    review_thread_id = config.get("review_thread_id")
    if not review_thread_id:
        logger.error("审核Thread ID未配置")
        return False

    review_thread = await bot.fetch_channel(review_thread_id)
    if not isinstance(review_thread, discord.Thread):
        logger.error(f"审核Thread配置错误: {review_thread_id}")
        return False

    if not guild_id:
        guild_id = review_thread.guild.id

    # 获取频道名称
    target_scope = application.target_scope
    if target_scope == "global":
        scope_text = "全频道"
    else:
        channels_dict = config.get("available_channels", {})
        scope_text = channels_dict.get(target_scope, f"频道 {target_scope}")

    # 构建审核embed
    embed = discord.Embed(
        title="🎨 新的Banner申请",
        color=discord.Color.orange(),
    )
    embed.add_field(name="申请人", value=f"<@{application.applicant_id}>", inline=True)
    embed.add_field(name="展示范围", value=scope_text, inline=True)

    # 构建帖子链接
    if guild_id:
        thread_link = f"https://discord.com/channels/{guild_id}/{application.thread_id}"
        embed.add_field(
            name="帖子",
            value=f"{thread_link}",
            inline=False,
        )
    else:
        embed.add_field(
            name="帖子ID",
            value=str(application.thread_id),
            inline=False,
        )

    embed.set_image(url=application.cover_image_url)
    embed.set_footer(text=f"申请ID: {application.id}")

    # 创建审核视图
    review_view = ReviewView(
        bot=bot,
        session_factory=session_factory,
        config=config,
    )

    try:
        review_message = await review_thread.send(embed=embed, view=review_view)

        # 更新申请记录的消息ID
        async with session_factory() as session:
            service = BannerService(session)
            await service.update_review_message_info(
                application.id, review_message.id, review_thread_id
            )

        logger.info(f"已发送审核消息，申请ID: {application.id}")
        return True

    except Exception as e:
        logger.error(f"发送审核消息失败: {e}", exc_info=True)
        return False


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

    async def validate_application_request(
        self,
        thread_id: int,
        applicant_id: int,
        cover_image_url: str,
        target_scope: Optional[str] = None,
    ) -> ApplicationResult:
        """
        验证Banner申请请求（不创建申请）

        用于在用户选择展示范围前进行预验证

        Args:
            thread_id: 帖子ID
            applicant_id: 申请人用户ID
            cover_image_url: 封面图URL
            target_scope: 展示范围 ('global' 或频道ID)，可选

        Returns:
            ApplicationResult: 包含成功状态、消息和帖子信息
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

        # 验证帖子存在
        result = await self.session.execute(
            select(Thread).where(Thread.thread_id == thread_id).options(selectinload(Thread.tags))
        )
        thread = result.scalar_one_or_none()

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
        验证并创建Banner申请

        完整的申请流程，包括：
        - 验证帖子存在
        - 验证申请人是帖子作者
        - 验证封面图URL格式
        - 验证展示范围
        - 创建申请记录

        Args:
            thread_id: 帖子ID
            applicant_id: 申请人用户ID
            cover_image_url: 封面图URL
            target_scope: 展示范围 ('global' 或频道ID)

        Returns:
            ApplicationResult: 包含成功状态、消息、申请记录和帖子信息
        """
        # 先进行验证
        validation = await self.validate_application_request(
            thread_id=thread_id,
            applicant_id=applicant_id,
            cover_image_url=cover_image_url,
            target_scope=target_scope,
        )

        if not validation.success:
            return validation

        thread = validation.thread
        cover_url = cover_image_url.strip()
        scope = target_scope.strip()

        # 创建申请
        application = await self.create_application(
            thread_id=thread_id,
            channel_id=thread.channel_id,
            applicant_id=applicant_id,
            cover_image_url=cover_url,
            target_scope=scope,
        )

        logger.info(
            f"用户 {applicant_id} 提交了Banner申请，帖子ID: {thread_id}，范围: {scope}"
        )

        return ApplicationResult(
            success=True,
            message="Banner申请已提交，等待审核",
            application=application,
            thread=thread,
        )

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
            applied_at=datetime.now(timezone.utc).replace(tzinfo=None),
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
        application.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
        application.reviewed_at = datetime.now(timezone.utc).replace(tzinfo=None)
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
        start_time = datetime.now(timezone.utc).replace(tzinfo=None)
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
            queued_at=datetime.now(timezone.utc).replace(tzinfo=None),
            position=new_position,
        )
        self.session.add(waitlist_item)

    async def _get_active_banner_count(self, channel_id: Optional[int]) -> int:
        """获取当前活跃的banner数量"""
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        result = await self.session.execute(
            select(BannerCarousel).where(
                and_(
                    BannerCarousel.channel_id == channel_id,
                    BannerCarousel.end_time > now,
                )
            )
        )
        return len(result.scalars().all())

    async def cleanup_expired_banners(self) -> int:
        """清理过期的banner并从等待列表补充"""
        now = datetime.now(timezone.utc).replace(tzinfo=None)

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

    async def delete_banner_by_thread(
        self, thread_id: int
    ) -> DeleteBannerResult:
        """
        根据 thread_id 从轮播或等待列表中删除 Banner。

        如果 Banner 在轮播中，删除后会尝试从等待队列晋升替补。
        """
        # 查询轮播列表
        carousel_result = await self.session.execute(
            select(BannerCarousel).where(BannerCarousel.thread_id == thread_id)
        )
        carousel_items = carousel_result.scalars().all()

        # 查询等待列表
        waitlist_result = await self.session.execute(
            select(BannerWaitlist).where(BannerWaitlist.thread_id == thread_id)
        )
        waitlist_items = waitlist_result.scalars().all()

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

            # 删除前检查是否有等待项可晋升
            has_waiting = await self._has_waitlist_item(channel_id)

            await self.session.delete(banner)

            # 从等待队列晋升
            if has_waiting:
                await self._promote_from_waitlist(channel_id)

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

            await self.session.delete(banner)
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

    async def _has_waitlist_item(self, channel_id: Optional[int]) -> bool:
        """检查等待列表中是否有指定频道的项"""
        result = await self.session.execute(
            select(BannerWaitlist)
            .where(BannerWaitlist.channel_id == channel_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

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
        now = datetime.now(timezone.utc).replace(tzinfo=None)

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

    async def get_application_by_review_message(
        self, review_message_id: int
    ) -> Optional[BannerApplication]:
        """通过审核消息ID获取申请记录"""
        result = await self.session.execute(
            select(BannerApplication).where(
                BannerApplication.review_message_id == review_message_id
            )
        )
        return result.scalar_one_or_none()
