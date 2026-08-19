import logging
import os
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.thread_repository import ThreadRepository
from shared.redis_client import RedisManager
from shared.safe_defer import safe_defer
from update_detector.deepseek_service import DeepSeekService
from update_detector.prompt_builder import build_update_detection_prompt
from update_detector.token_estimator import TokenEstimator
from update_detector.token_stats_service import TokenStatsService
from update_detector.update_preference_service import UpdatePreferenceService
from update_detector.views import UpdateDetectorView, build_update_embed

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


class UpdateDetector(commands.Cog):
    """监听已索引帖子中的消息，检测发布更新并提醒用户同步"""

    VALID_MODES = {"disabled", "observe", "active"}

    def __init__(
        self,
        bot: "MyBot",
        session_factory: async_sessionmaker,
        config: dict,
    ):
        self.bot = bot
        self.session_factory = session_factory
        self.config = config
        self.cache_service = bot.cache_service

        ud_config = config.get("update_detector", {})
        configured_mode = ud_config.get("mode", "disabled")
        if (
            not isinstance(configured_mode, str)
            or configured_mode not in self.VALID_MODES
        ):
            logger.warning(
                "更新检测 mode 非法，已按 disabled 处理: mode=%r",
                configured_mode,
            )
            configured_mode = "disabled"
        self.mode = configured_mode
        self.prompt_message = ud_config.get(
            "prompt_message", "检测到您可能发布了作品的新版本，是否将其同步到索引页？"
        )
        self.min_text_length = ud_config.get("min_text_length", 100)
        self.min_text_length_with_attachment = ud_config.get(
            "min_text_length_with_attachment", 30
        )
        self.deepseek_model = ud_config.get("deepseek_model", "deepseek-v4-flash")
        self.thinking_enabled = ud_config.get("thinking_enabled", True)
        self.max_output_tokens = ud_config.get("max_output_tokens", 2048)
        self.token_estimator = TokenEstimator()
        self.token_stats_service: TokenStatsService | None = None
        if self.mode != "disabled":
            self.token_stats_service = TokenStatsService(RedisManager.get_client())

        self.deepseek_service: DeepSeekService | None = None
        deepseek_api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if self.mode == "active" and deepseek_api_key:
            self.deepseek_service = DeepSeekService(
                api_key=deepseek_api_key,
                base_url=ud_config.get("deepseek_base_url", "https://api.deepseek.com"),
                model=self.deepseek_model,
                thinking_enabled=self.thinking_enabled,
                max_output_tokens=self.max_output_tokens,
                timeout_seconds=ud_config.get("request_timeout_seconds", 60),
            )
        elif self.mode == "active":
            logger.error("更新检测 active 模式缺少 DEEPSEEK_API_KEY，AI 调用已停用")

        logger.info("UpdateDetector 已加载 (mode=%s)", self.mode)

    async def cog_unload(self):
        if self.deepseek_service:
            await self.deepseek_service.close()

    def _is_channel_indexed(self, channel_id: int) -> bool:
        return self.cache_service.is_channel_indexed(channel_id)

    def _is_potential_update(self, message: discord.Message) -> bool:
        """
        初步筛选：判断消息是否可能是发布更新。
        条件：
        1. 大量文字（>=min_text_length）
        2. 中量文字 + png 图片附件
        3. 中量文字 + json 文件附件
        """
        text_len = len(message.content)
        has_png = any(
            att.filename.lower().endswith(".png") for att in message.attachments
        )
        has_json = any(
            att.filename.lower().endswith(".json") for att in message.attachments
        )
        has_relevant_attachment = has_png or has_json

        if has_relevant_attachment:
            return text_len >= self.min_text_length_with_attachment
        return text_len >= self.min_text_length

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if self.mode == "disabled":
            return
        if self.mode == "active" and not self.deepseek_service:
            return

        if (
            not message.guild
            or not isinstance(message.channel, discord.Thread)
            or message.author.bot
        ):
            return

        thread = message.channel

        # 只处理已索引频道中的帖子
        if not self._is_channel_indexed(thread.parent_id):
            return

        # 只处理帖子作者发送的消息（非首楼）
        if thread.owner_id != message.author.id:
            return
        if thread.id == message.id:
            return

        if not self._is_potential_update(message):
            return

        # 检查用户偏好
        async with self.session_factory() as session:
            pref_service = UpdatePreferenceService(session)
            pref = await pref_service.get_preference(message.author.id, thread.id)

        if pref and pref.no_remind:
            return

        # 构造本地估算和正式调用共用的判断内容
        attachment_filenames = None
        json_filenames = [
            att.filename
            for att in message.attachments
            if att.filename.lower().endswith(".json")
        ]
        if json_filenames:
            attachment_filenames = json_filenames

        system_content, user_content = build_update_detection_prompt(
            message.content,
            attachment_filenames,
        )
        if self.mode == "observe":
            estimate = self.token_estimator.estimate(
                system_content=system_content,
                user_content=user_content,
                max_output_tokens=self.max_output_tokens,
            )
            if self.token_stats_service:
                await self.token_stats_service.record_estimate(
                    model=self.deepseek_model,
                    thinking_enabled=self.thinking_enabled,
                    max_output_tokens=self.max_output_tokens,
                    estimate=estimate,
                )
            return

        result = await self.deepseek_service.detect_update(
            system_content=system_content,
            user_content=user_content,
        )
        if self.token_stats_service:
            await self.token_stats_service.record_actual(
                model=self.deepseek_model,
                thinking_enabled=self.thinking_enabled,
                max_output_tokens=self.max_output_tokens,
                result=result,
            )
        if not result.is_update:
            return

        message_link = message.jump_url

        # 如果用户设置了自动同步，直接执行同步
        if pref and pref.auto_sync:
            success = await self.do_sync_update(thread.id, message_link)
            if success:
                logger.info(f"帖子 {thread.id} 自动同步更新: {message_link}")
            return

        # 发送提醒 embed
        embed = build_update_embed(self.prompt_message, thread.name)
        view = UpdateDetectorView(
            cog=self,
            thread_id=thread.id,
            author_id=message.author.id,
            message_link=message_link,
        )

        try:
            sent_message = await self.bot.api_scheduler.submit(
                coro_factory=lambda: message.reply(embed=embed, view=view),
                priority=5,
            )
            view.set_message(sent_message)
        except Exception:
            logger.error(f"发送更新检测提醒失败 (帖子 {thread.id})", exc_info=True)

    async def do_sync_update(self, thread_id: int, message_link: str) -> bool:
        """执行同步更新操作"""
        async with self.session_factory() as session:
            repo = ThreadRepository(session)
            return await repo.update_thread_update_info(
                thread_id=thread_id, latest_update_link=message_link
            )

    async def set_user_auto_sync(
        self, user_id: int, thread_id: int, enabled: bool
    ) -> None:
        async with self.session_factory() as session:
            pref_service = UpdatePreferenceService(session)
            await pref_service.set_auto_sync(user_id, thread_id, enabled)

    async def set_user_no_remind(
        self, user_id: int, thread_id: int, enabled: bool
    ) -> None:
        async with self.session_factory() as session:
            pref_service = UpdatePreferenceService(session)
            await pref_service.set_no_remind(user_id, thread_id, enabled)

    # ── 用户指令：管理更新提醒偏好 ──

    # update_remind_group = app_commands.Group(
    #     name="更新提醒设置", description="管理帖子更新检测的提醒偏好"
    # )

    # @update_remind_group.command(name="查看", description="查看当前帖子的更新提醒设置")
    async def view_preference(self, interaction: discord.Interaction):
        await safe_defer(interaction)

        if not isinstance(interaction.channel, discord.Thread):
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    "❌ 此命令只能在帖子中使用。", ephemeral=True
                ),
                priority=1,
            )
            return

        thread = interaction.channel
        async with self.session_factory() as session:
            pref_service = UpdatePreferenceService(session)
            pref = await pref_service.get_preference(interaction.user.id, thread.id)

        auto_sync = pref.auto_sync if pref else False
        no_remind = pref.no_remind if pref else False

        embed = discord.Embed(
            title="📦 更新提醒设置",
            description=f"帖子: **{thread.name}**",
            color=discord.Color.blue(),
        )
        embed.add_field(
            name="自动同步",
            value="✅ 已开启" if auto_sync else "❌ 未开启",
            inline=True,
        )
        embed.add_field(
            name="更新提醒",
            value="🔕 已关闭" if no_remind else "🔔 已开启",
            inline=True,
        )
        embed.set_footer(text="使用 /更新提醒设置 修改 来更改这些设置")

        await self.bot.api_scheduler.submit(
            coro_factory=lambda: interaction.followup.send(embed=embed, ephemeral=True),
            priority=1,
        )

    # @update_remind_group.command(
    #     name="修改", description="修改当前帖子的更新提醒设置"
    # )
    # @app_commands.describe(
    #     自动同步="是否自动同步更新到索引页（无需确认）",
    #     不再提醒="是否关闭此帖的更新检测提醒",
    # )
    # @app_commands.choices(
    #     自动同步=[
    #         app_commands.Choice(name="开启", value=1),
    #         app_commands.Choice(name="关闭", value=0),
    #     ],
    #     不再提醒=[
    #         app_commands.Choice(name="开启（不再提醒）", value=1),
    #         app_commands.Choice(name="关闭（恢复提醒）", value=0),
    #     ],
    # )
    async def modify_preference(
        self,
        interaction: discord.Interaction,
        自动同步: app_commands.Choice[int] | None = None,
        不再提醒: app_commands.Choice[int] | None = None,
    ):
        await safe_defer(interaction)

        if not isinstance(interaction.channel, discord.Thread):
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    "❌ 此命令只能在帖子中使用。", ephemeral=True
                ),
                priority=1,
            )
            return

        thread = interaction.channel

        if 自动同步 is None and 不再提醒 is None:
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    "❌ 请至少指定一个设置项。", ephemeral=True
                ),
                priority=1,
            )
            return

        changes = []
        async with self.session_factory() as session:
            pref_service = UpdatePreferenceService(session)

            if 自动同步 is not None:
                enabled = 自动同步.value == 1
                await pref_service.set_auto_sync(
                    interaction.user.id, thread.id, enabled
                )
                changes.append(f"自动同步: {'✅ 已开启' if enabled else '❌ 已关闭'}")

            if 不再提醒 is not None:
                enabled = 不再提醒.value == 1
                await pref_service.set_no_remind(
                    interaction.user.id, thread.id, enabled
                )
                changes.append(f"更新提醒: {'🔕 已关闭' if enabled else '🔔 已恢复'}")

        result_text = "\n".join(changes)
        await self.bot.api_scheduler.submit(
            coro_factory=lambda: interaction.followup.send(
                f"✅ 设置已更新：\n{result_text}", ephemeral=True
            ),
            priority=1,
        )

    # @update_remind_group.command(
    #     name="重置", description="重置当前帖子的更新提醒设置为默认值"
    # )
    async def reset_preference(self, interaction: discord.Interaction):
        await safe_defer(interaction)

        if not isinstance(interaction.channel, discord.Thread):
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    "❌ 此命令只能在帖子中使用。", ephemeral=True
                ),
                priority=1,
            )
            return

        thread = interaction.channel
        async with self.session_factory() as session:
            pref_service = UpdatePreferenceService(session)
            success = await pref_service.reset_preference(
                interaction.user.id, thread.id
            )

        if success:
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    "✅ 已重置为默认设置（提醒开启、自动同步关闭）。",
                    ephemeral=True,
                ),
                priority=1,
            )
        else:
            await self.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    "ℹ️ 当前帖子没有自定义设置，无需重置。", ephemeral=True
                ),
                priority=1,
            )
