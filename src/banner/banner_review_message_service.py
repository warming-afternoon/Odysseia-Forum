import logging

import discord
from sqlalchemy import select

from banner.banner_service import BannerService
from banner.dto.banner_review_application import BannerReviewApplication
from banner.views.review_embed_builder import ReviewEmbedBuilder
from banner.views.review_view import ReviewView
from core.banner_application_repository import BannerApplicationRepository
from core.thread_repository import ThreadRepository
from models import Channel
from shared.enum import ApplicationStatus, TargetType

logger = logging.getLogger(__name__)


class BannerReviewMessageService:
    """统一发送审核消息并协调延迟投递与审核事务的状态同步。"""

    def __init__(self, bot, session_factory, config: dict):
        self.bot = bot
        self.session_factory = session_factory
        self.config = config

    async def send(
        self, application_id: int, guild_id: int | None = None
    ) -> BannerReviewApplication | None:
        """发送当前状态的审核消息，回填后再次同步并发审核结果。"""
        try:
            async with self.session_factory() as session:
                service = BannerService(session)
                application = await service.get_review_snapshot(application_id)
                if application is None:
                    logger.warning("审核投递中的申请已不存在: %s", application_id)
                    return None
                if application.review_message_id is not None:
                    return application
                cover = application.cover_image_url
                if application.target_type == TargetType.CHANNEL.value:
                    result = await session.execute(
                        select(Channel).where(
                            Channel.channel_id == application.thread_id
                        )
                    )
                    channel = result.scalar_one_or_none()
                    guild_id = channel.guild_id if channel else guild_id
                else:
                    thread_repo = ThreadRepository(session)
                    guild_id = (
                        await thread_repo.get_thread_guild_id(application.thread_id)
                        or guild_id
                    )
                    if cover is None:
                        thread = await thread_repo.get_thread_with_tags(
                            application.thread_id
                        )
                        if thread and thread.thumbnail_urls:
                            cover = thread.thumbnail_urls[0]
                history = await BannerApplicationRepository(
                    session
                ).get_history_by_thread_id(application.thread_id)
                embed = ReviewEmbedBuilder.build_review_embed(
                    application, self.config, guild_id, history or None, cover
                )
                ReviewEmbedBuilder.apply_review_result(embed, application)

            # Discord I/O 在数据库会话外执行，投递前的状态只控制初始按钮。
            thread_id = self.config.get("review_thread_id")
            if not thread_id:
                logger.error("审核频道未配置")
                return None
            channel = await self.bot.fetch_channel(thread_id)
            if not isinstance(channel, discord.Thread):
                logger.error("审核频道配置错误: %s", thread_id)
                return None
            view = (
                ReviewView()
                if application.status == ApplicationStatus.PENDING.value
                else None
            )
            message = await channel.send(embed=embed, view=view)
            async with self.session_factory() as session:
                latest = await BannerService(session).update_review_message_info(
                    application_id, message.id, thread_id
                )

            # 加锁回填返回的快照覆盖发送期间发生的批准或自动拒绝。
            if latest is not None and latest.status != ApplicationStatus.PENDING.value:
                try:
                    ReviewEmbedBuilder.apply_review_result(embed, latest)
                    await message.edit(embed=embed, view=None)
                except Exception:
                    logger.warning(
                        "同步延迟审核消息失败: %s", application_id, exc_info=True
                    )
            return latest
        except Exception:
            logger.error("发送 Banner 审核消息失败: %s", application_id, exc_info=True)
            return None
