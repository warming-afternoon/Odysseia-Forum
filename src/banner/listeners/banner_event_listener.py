"""Banner 事件监听器 —— 订阅 View 分发的自定义事件并协调 Service + Discord I/O。"""

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING

import discord
from discord.ext import commands, tasks
from sqlalchemy.ext.asyncio import async_sessionmaker

from banner.banner_service import BannerService
from banner.banner_review_message_service import BannerReviewMessageService
from banner.banner_review_notification_service import BannerReviewNotificationService
from banner.channel_sync import ChannelSyncService
from banner.views.channel_selection_view import ChannelSelectionView
from shared.enum import TargetType
from shared.redis_client import RedisManager

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


class BannerEventListener(commands.Cog):
    """监听 banner 相关自定义事件，持有 Service 和 View 构建器。"""

    def __init__(
        self,
        bot: "MyBot",
        session_factory: async_sessionmaker,
        config: dict,
    ):
        self.bot = bot
        self.session_factory = session_factory
        self.config = config
        # 与 Bot 启动使用相同凭据，为未入库频道提供按需索引。
        bot_token = os.environ.get("BOT_TOKEN", "").strip()
        self.channel_sync = ChannelSyncService(bot_token) if bot_token else None
        logger.info("Banner事件监听器已加载")

    async def cog_load(self):
        """Cog 加载时启动后台任务。"""
        self._consume_banner_review_queue.start()
        logger.info("Banner 审核队列消费者已启动")

    async def cog_unload(self):
        """Cog 卸载时停止后台任务。"""
        self._consume_banner_review_queue.cancel()

    # ── Banner 审核队列消费 ────────────────────────────────────

    @tasks.loop(seconds=1)
    async def _consume_banner_review_queue(self):
        """后台循环：消费 Redis 中的 banner 审核消息队列，发送审核消息到 Discord。"""
        redis = RedisManager.get_client()

        try:
            result = await redis.brpop("banner:review:queue", timeout=5)  # type: ignore[return-type]
            if result is None:
                return
            _, payload = result
            data = json.loads(payload)
            application_id = data["application_id"]

            # 统一投递服务读取最新状态，不为已拒绝申请重建审核按钮。
            await BannerReviewMessageService(
                self.bot, self.session_factory, self.config
            ).send(application_id)
        except Exception:
            logger.error("消费 Banner 审核队列时出错", exc_info=True)
            await asyncio.sleep(5)

    @_consume_banner_review_queue.before_loop
    async def _before_consume_queue(self):
        """等待 bot 准备就绪后开始消费队列。"""
        await self.bot.wait_until_ready()

    # ── 表单提交 → 预验证 → 展示频道选择 ──────────────────────

    @commands.Cog.listener()
    async def on_banner_form_submit(
        self,
        interaction: discord.Interaction,
        target_id: int,
        cover_image_url: str | None,
        guild_id: int,
    ):
        """处理 banner_form_submit 事件：预验证目标 → 展示 ChannelSelectionView。"""
        try:
            async with self.session_factory() as session:
                service = BannerService(session, channel_sync=self.channel_sync)
                # 表单阶段复查，避免用户填写期间已有其他申请获批。
                if await service.has_ongoing_banner(interaction.user.id):
                    await interaction.followup.send(
                        f"❌ {service.APPLICANT_LIMIT_MESSAGE}", ephemeral=True
                    )
                    return
                validation = await service.validate_application_request(
                    target_id=target_id,
                    guild_id=guild_id,
                    applicant_id=interaction.user.id,
                    cover_image_url=cover_image_url,
                )

                if not validation.success:
                    await interaction.followup.send(
                        f"❌ {validation.message}", ephemeral=True
                    )
                    return

                # 渠道目标使用 target_name，论坛帖子使用 thread.title
                if validation.target_type == TargetType.CHANNEL.value:
                    target_title = validation.target_name
                    target_channel_id = target_id
                elif validation.thread:
                    target_title = validation.thread.title
                    target_channel_id = validation.thread.channel_id
                else:
                    await interaction.followup.send(
                        "❌ 无法获取有效的目标信息，请检查链接或ID并联系管理员。",
                        ephemeral=True,
                    )
                    return

            # 构建目标链接
            target_link = f"https://discord.com/channels/{guild_id}/{target_id}"

            # 展示频道选择视图
            view = ChannelSelectionView(
                available_channels=self.config.get("available_channels", {}),
                thread_id=target_id,
                channel_id=target_channel_id,
                cover_image_url=cover_image_url,
                applicant_id=interaction.user.id,
                thread_title=target_title,
                thread_link=target_link,
                guild_id=guild_id,
            )
            await view.send_to(interaction)

        except Exception:
            logger.error("处理 banner_form_submit 事件时出错", exc_info=True)
            try:
                await interaction.followup.send(
                    "❌ 处理申请时出错，请稍后重试", ephemeral=True
                )
            except Exception:
                pass

    # ── 频道选择 → 创建申请 → 发送审核消息 ──────────────────────

    @commands.Cog.listener()
    async def on_banner_apply(
        self,
        interaction: discord.Interaction,
        thread_id: int,
        cover_image_url: str | None,
        target_scope: str,
        applicant_id: int,
        channel_id: int,
        guild_id: int = 0,
    ):
        """处理 banner_apply 事件：创建申请 → 发送审核消息 → 通知用户。"""
        try:
            async with self.session_factory() as session:
                service = BannerService(session, channel_sync=self.channel_sync)

                result = await service.validate_and_create_application(
                    target_id=thread_id,
                    guild_id=guild_id,
                    applicant_id=applicant_id,
                    cover_image_url=cover_image_url,
                    target_scope=target_scope,
                    enforce_applicant_limit=True,
                )

                if not result.success:
                    await interaction.followup.send(
                        f"❌ {result.message}", ephemeral=True
                    )
                    return

                application = result.application
                if application is None or application.id is None:
                    await interaction.followup.send(
                        "❌ 申请创建失败，请重试", ephemeral=True
                    )
                    return

                application_id = application.id

            # 创建事务结束后统一投递，并按返回的最新状态反馈申请人。
            latest = await BannerReviewMessageService(
                self.bot, self.session_factory, self.config
            ).send(application_id, guild_id)
            if latest is None:
                await interaction.followup.send(
                    "❌ 申请已创建，但审核消息发送失败，请联系管理员", ephemeral=True
                )
                return
            message = "✅ 申请已提交！审核员将尽快处理您的申请。"
            if latest.status != "pending":
                message = f"✅ 申请已提交，当前审核状态: {latest.status}"
            await interaction.followup.send(message, ephemeral=True)

        except Exception:
            logger.error("处理 banner_apply 事件时出错", exc_info=True)
            try:
                await interaction.followup.send(
                    "❌ 提交申请时发生错误，请稍后重试", ephemeral=True
                )
            except Exception:
                pass

    # ── 审核：批准 ────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_banner_review_approve(
        self,
        interaction: discord.Interaction,
        reviewer_id: int,
    ):
        """处理 banner_review_approve 事件：通过消息 ID 查找申请 → 批准。"""
        await interaction.response.defer(ephemeral=True)

        try:
            async with self.session_factory() as session:
                service = BannerService(session)
                application = await service.get_application_by_review_message(
                    interaction.message.id
                )

                if not application:
                    await interaction.followup.send(
                        "❌ 找不到对应的申请记录，可能已被处理或数据丢失",
                        ephemeral=True,
                    )
                    return

                if application.status != "pending":
                    await interaction.followup.send(
                        f"❌ 该申请已被处理，当前状态: {application.status}",
                        ephemeral=True,
                    )
                    return

                application_id = application.id
                if application_id is None:
                    await interaction.followup.send("❌ 申请数据异常", ephemeral=True)
                    return

                result = await service.approve_application(application_id, reviewer_id)

            # 服务已提交事务，后续只使用 DTO，并逐项隔离 Discord 失败。
            notifier = BannerReviewNotificationService(self.bot, self.config)
            await notifier.notify(
                result.application, result.entered_carousel, interaction.message
            )
            for rejected in result.auto_rejected:
                await notifier.notify(rejected)
            result_msg = "✅ 已同意申请"
            result_msg += (
                "\n🎨 Banner已加入轮播列表"
                if result.entered_carousel
                else "\n⏳ Banner已加入等待列表"
            )
            result_msg += f"\n已自动拒绝其他 {len(result.auto_rejected)} 个待审核申请"
            await interaction.followup.send(result_msg, ephemeral=True)

        except ValueError as error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
        except Exception:
            logger.error("处理 banner_review_approve 事件时出错", exc_info=True)
            try:
                await interaction.followup.send(
                    "❌ 处理失败，请稍后重试", ephemeral=True
                )
            except Exception:
                pass

    # ── 审核：拒绝 ────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_banner_review_reject(
        self,
        interaction: discord.Interaction,
        reviewer_id: int,
        reason: str,
    ):
        """处理 banner_review_reject 事件：通过消息 ID 查找申请 → 拒绝。"""
        await interaction.response.defer(ephemeral=True)

        try:
            async with self.session_factory() as session:
                service = BannerService(session)
                application = await service.get_application_by_review_message(
                    interaction.message.id
                )

                if not application:
                    await interaction.followup.send(
                        "❌ 找不到对应的申请记录，可能已被处理或数据丢失",
                        ephemeral=True,
                    )
                    return

                if application.status != "pending":
                    await interaction.followup.send(
                        f"❌ 该申请已被处理，当前状态: {application.status}",
                        ephemeral=True,
                    )
                    return

                application_id = application.id
                if application_id is None:
                    await interaction.followup.send("❌ 申请数据异常", ephemeral=True)
                    return

                application = await service.reject_application(
                    application_id, reviewer_id, reason
                )

            await BannerReviewNotificationService(self.bot, self.config).notify(
                application, message=interaction.message
            )
            await interaction.followup.send("✅ 已拒绝申请", ephemeral=True)

        except ValueError as error:
            await interaction.followup.send(f"❌ {error}", ephemeral=True)
        except Exception:
            logger.error("处理 banner_review_reject 事件时出错", exc_info=True)
            try:
                await interaction.followup.send(
                    "❌ 处理失败，请稍后重试", ephemeral=True
                )
            except Exception:
                pass
