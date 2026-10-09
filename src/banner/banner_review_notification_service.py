import logging

import discord

from banner.dto.banner_review_application import BannerReviewApplication
from banner.views.review_embed_builder import ReviewEmbedBuilder
from shared.enum import ApplicationStatus

logger = logging.getLogger(__name__)


class BannerReviewNotificationService:
    """审核提交后独立执行消息更新、私信及归档，并隔离投递失败。"""

    def __init__(self, bot, config: dict):
        self.bot = bot
        self.config = config

    async def notify(
        self,
        application: BannerReviewApplication,
        entered_carousel: bool | None = None,
        message=None,
    ) -> None:
        """为已审核申请执行完整后续，单项失败不阻断其他动作。"""
        # 更新原消息失败不影响私信、归档或其他自动拒绝申请。
        try:
            if (
                message is None
                and application.review_thread_id
                and application.review_message_id
            ):
                channel = await self.bot.fetch_channel(application.review_thread_id)
                message = await channel.fetch_message(application.review_message_id)
            if message is not None:
                embed = (
                    message.embeds[0].copy()
                    if message.embeds
                    else discord.Embed(title="Banner审核")
                )
                ReviewEmbedBuilder.apply_review_result(
                    embed, application, entered_carousel
                )
                await message.edit(embed=embed, view=None)
        except Exception:
            logger.warning("更新审核消息失败: %s", application.id, exc_info=True)

        # 私信仍沿用每条申请一个结果，自动拒绝理由指向通过的申请。
        try:
            applicant = await self.bot.fetch_user(application.applicant_id)
            if application.status == ApplicationStatus.REJECTED.value:
                embed = ReviewEmbedBuilder.build_reject_dm(
                    application, application.reject_reason or ""
                )
            else:
                embed = ReviewEmbedBuilder.build_approve_dm(
                    application, bool(entered_carousel)
                )
            await applicant.send(embed=embed)
        except Exception:
            logger.warning("发送审核私信失败: %s", application.id, exc_info=True)

        # 存档采用已提交快照，不重新操作申请状态。
        try:
            status = "rejected"
            if application.status == ApplicationStatus.APPROVED.value:
                status = (
                    "approved_carousel" if entered_carousel else "approved_waitlist"
                )
            await ReviewEmbedBuilder.archive_review(
                self.bot, self.config, application, status, application.reviewer_id
            )
        except Exception:
            logger.warning("存档审核记录失败: %s", application.id, exc_info=True)
