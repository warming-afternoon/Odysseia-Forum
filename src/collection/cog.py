import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.ext.asyncio import async_sessionmaker
from typing import TYPE_CHECKING
import logging

from shared.safe_defer import safe_defer
from .collection_service import CollectionService

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


class CollectionCog(commands.Cog):
    """处理帖子收藏相关功能"""

    def __init__(self, bot: "MyBot", session_factory: async_sessionmaker):
        self.bot = bot
        self.session_factory = session_factory
        logger.info("CollectionCog 模块已加载")

    async def cog_load(self):
        """在Cog加载时注册上下文菜单"""
        collect_menu = app_commands.ContextMenu(
            name="收藏此帖",
            callback=self.collect_thread_context_menu,
        )
        remove_menu = app_commands.ContextMenu(
            name="移除收藏",
            callback=self.remove_collection_context_menu,
        )
        self.bot.tree.add_command(collect_menu)
        self.bot.tree.add_command(remove_menu)

    async def collect_thread_context_menu(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        """右键菜单命令：收藏此帖"""
        await safe_defer(interaction, ephemeral=True)

        if not isinstance(message.channel, discord.Thread):
            await interaction.followup.send(
                "❌ 此命令只能对帖子内的消息使用。", ephemeral=True
            )
            return

        thread_id = message.channel.id
        user_id = interaction.user.id

        async with self.session_factory() as session:
            service = CollectionService(session)
            success = await service.add_collection(user_id=user_id, thread_id=thread_id)

        if success:
            await interaction.followup.send(
                f"✅ **{message.channel.name}** 已成功添加至您的收藏！", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"🤔 您之前已经收藏过 **{message.channel.name}** 了。", ephemeral=True
            )

    async def remove_collection_context_menu(
        self, interaction: discord.Interaction, message: discord.Message
    ):
        """右键菜单命令：移除收藏"""
        await safe_defer(interaction, ephemeral=True)

        if not isinstance(message.channel, discord.Thread):
            await interaction.followup.send(
                "❌ 此命令只能对帖子内的消息使用。", ephemeral=True
            )
            return

        thread_id = message.channel.id
        user_id = interaction.user.id

        async with self.session_factory() as session:
            service = CollectionService(session)
            success = await service.remove_collection(
                user_id=user_id, thread_id=thread_id
            )

        if success:
            await interaction.followup.send(
                f"🗑️ **{message.channel.name}** 已从您的收藏中移除。", ephemeral=True
            )
        else:
            await interaction.followup.send(
                f"🤔 您尚未收藏过 **{message.channel.name}**。", ephemeral=True
            )
