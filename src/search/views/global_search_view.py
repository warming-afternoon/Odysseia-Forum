import discord
from typing import TYPE_CHECKING

from shared.safe_defer import safe_defer
from .preferences_view import PreferencesView


if TYPE_CHECKING:
    from ..cog import Search


class GlobalSearchView(discord.ui.View):
    """全局搜索命令的入口视图。"""

    def __init__(self, cog: "Search"):
        super().__init__(timeout=None)  # 持久化视图
        self.cog = cog

    @discord.ui.button(
        label="🌐 开始搜索",
        style=discord.ButtonStyle.success,
        custom_id="global_search_button",
        row=0,
    )
    async def start_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """处理按钮点击，启动全局搜索流程。"""
        await self.cog._start_global_search(interaction)

    @discord.ui.button(
        label="⚙️ 偏好设置",
        style=discord.ButtonStyle.secondary,
        custom_id="global_search_preferences_button",
        row=0,
    )
    async def preferences_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        """打开搜索偏好设置面板。"""
        await safe_defer(interaction, ephemeral=True)
        view = PreferencesView(self.cog.prefs_handler, interaction)
        await view.start()
