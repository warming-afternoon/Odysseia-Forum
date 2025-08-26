import discord
from typing import TYPE_CHECKING

from shared.safe_defer import safe_defer
from .channel_selection_view import ChannelSelectionView
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
        await safe_defer(interaction, ephemeral=True)

        async with self.cog.session_factory() as session:
            repo = self.cog.tag_system_repo(session)
            indexed_channel_ids = await repo.get_indexed_channel_ids()
        if not indexed_channel_ids:
            await self.cog.bot.api_scheduler.submit(
                coro_factory=lambda: interaction.followup.send(
                    "没有已索引的频道。", ephemeral=True
                ),
                priority=1,
            )
            return

        channels = [
            ch
            for ch_id in indexed_channel_ids
            if (ch := self.cog.bot.get_channel(ch_id))
            and isinstance(ch, discord.ForumChannel)
        ]

        # 获取用户偏好 DTO
        async with self.cog.session_factory() as session:
            from ..repository import SearchRepository
            repo = SearchRepository(session, self.cog.tag_service)
            user_prefs = await repo.get_user_preferences(interaction.user.id)

        view = ChannelSelectionView(
            self.cog, interaction, channels, indexed_channel_ids, user_prefs
        )

        message_content = "请选择要搜索的论坛频道（可多选）："
        if user_prefs and user_prefs.preferred_channels:
            message_content = "已根据偏好预选了频道，可以直接点击“确定搜索”继续或进行修改"

        await self.cog.bot.api_scheduler.submit(
            coro_factory=lambda: interaction.followup.send(
                message_content, view=view, ephemeral=True
            ),
            priority=1,
        )

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
