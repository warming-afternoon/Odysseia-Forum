import json
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from dto.events.tag_command import TagCommand
from shared.tag_error import TagError
from tag.tag_runtime import create_tag_mediator
from tag.tag_worker import TagWorker

logger = logging.getLogger(__name__)


class TagCog(commands.Cog):
    """提供 BOT 标签管理命令与审核后台任务。"""

    def __init__(self, bot, session_factory, config):
        self.bot = bot
        self.config = config
        self.worker = TagWorker(session_factory, config)
        self.mediator = create_tag_mediator(session_factory, config)

    async def cog_load(self):
        """启动可恢复的审核和通知循环。"""
        self.expire.start()
        self.notify.start()

    async def cog_unload(self):
        """停止后台循环。"""
        self.expire.cancel()
        self.notify.cancel()

    @tasks.loop(minutes=1)
    async def expire(self):
        """自动处理满七天申请。"""
        try:
            await self.worker.expire()
        except Exception:
            logger.exception("标签审核循环失败")

    @tasks.loop(seconds=5)
    async def notify(self):
        """逐条发送持久化通知。"""
        try:
            await self.worker.deliver(self.send_notice)
        except Exception:
            logger.exception("标签通知循环失败")

    @notify.before_loop
    async def before_notify(self):
        """等待 BOT 连接完成。"""
        await self.bot.wait_until_ready()

    async def send_notice(self, proposal):
        """私信失败后在帖子提醒，书单和不可写帖子使用站内通知。"""
        frontend = self.config.get("auth", {}).get("frontend_url", "").rstrip("/")
        message = (
            f"你有一条待审核的标签提议（申请 {proposal.id}，"
            f"{'帖子' if proposal.target_type == 'thread' else '书单'} {proposal.target_id}）。"
            f"请前往索引页审核；提交满 7 天后将自动尝试生效。 {frontend}"
        )
        try:
            user = self.bot.get_user(proposal.owner_id) or await self.bot.fetch_user(
                proposal.owner_id
            )
            await user.send(message)
            return True
        except (discord.Forbidden, discord.NotFound):
            pass
        if proposal.target_type == "thread":
            try:
                channel = self.bot.get_channel(
                    proposal.target_id
                ) or await self.bot.fetch_channel(proposal.target_id)
                await channel.send(
                    f"<@{proposal.owner_id}> 有新的标签提议待审核，请前往索引页处理。 {frontend}",
                    allowed_mentions=discord.AllowedMentions(
                        users=[discord.Object(proposal.owner_id)],
                        roles=False,
                        everyone=False,
                    ),
                )
                return True
            except (discord.Forbidden, discord.NotFound):
                pass
        return False

    @app_commands.command(
        name="tag_manage", description="BOT 管理员维护标签池，提交 JSON 管理命令"
    )
    async def tag_manage(self, interaction: discord.Interaction, payload: str):
        """使用与管理 API 相同的领域命令维护标签。"""
        await interaction.response.defer(ephemeral=True)
        try:
            data = json.loads(payload)
            if not isinstance(data, dict):
                raise ValueError("管理命令必须是 JSON 对象")
            result = await self.mediator.request(
                TagCommand("manage", interaction.user.id, data)
            )
            await interaction.followup.send(
                json.dumps(result, ensure_ascii=False, default=str)[:1900],
                ephemeral=True,
            )
        except (TagError, ValueError) as exc:
            await interaction.followup.send(str(exc), ephemeral=True)
