"""共享的权限检查装饰器。"""

import logging
from typing import TYPE_CHECKING, cast

import discord
from discord import app_commands

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


async def check_is_admin_or_bot_admin(interaction: discord.Interaction) -> bool:
    """
    检查用户是否为服务器管理员或在 config.json 中定义的机器人管理员。

    可用于 View 按钮回调等非斜杠命令场景。
    """
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


def is_admin_or_bot_admin():
    """
    自定义 app_commands.check 装饰器，验证用户是否为服务器管理员或在 config.json 中定义的机器人管理员。

    用于斜杠命令的权限控制。
    """

    async def predicate(interaction: discord.Interaction) -> bool:
        return await check_is_admin_or_bot_admin(interaction)

    return app_commands.check(predicate)
