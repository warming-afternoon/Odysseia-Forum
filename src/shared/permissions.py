"""共享的权限检查装饰器。"""

import logging
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


def is_admin_or_bot_admin():
    """
    自定义检查函数，验证用户是否为服务器管理员或在 config.json 中定义的机器人管理员。
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        bot = cast("MyBot", interaction.client)

        if not hasattr(bot, "config"):
            return False

        bot_admin_ids = bot.config.get("bot_admin_user_ids", [])
        if interaction.user.id in bot_admin_ids:
            return True

        if (
            isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        ):
            return True

        return False

    return app_commands.check(predicate)
