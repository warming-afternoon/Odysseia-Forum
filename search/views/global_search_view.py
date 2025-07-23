import discord
from typing import List, TYPE_CHECKING

from .channel_selection_view import ChannelSelectionView

if TYPE_CHECKING:
    from ..cog import Search

class GlobalSearchView(discord.ui.View):
    """全局搜索命令的入口视图。"""
    def __init__(self, cog: "Search"):
        super().__init__(timeout=None) # 持久化视图
        self.cog = cog

    @discord.ui.button(label="🌐 全局搜索", style=discord.ButtonStyle.success, custom_id="global_search_button")
    async def start_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        """处理按钮点击，启动全局搜索流程。"""
        if not interaction.response.is_done():
            await self.cog.bot.api_scheduler.submit(
                coro=interaction.response.defer(ephemeral=True),
                priority=1
            )
        
        async with self.cog.session_factory() as session:
            repo = self.cog.tag_system_repo(session)
            indexed_channel_ids = await repo.get_indexed_channel_ids()
        if not indexed_channel_ids:
            await self.cog.bot.api_scheduler.submit(
                coro=interaction.followup.send("没有已索引的频道。", ephemeral=True),
                priority=1
            )
            return

        channels = [self.cog.bot.get_channel(ch_id) for ch_id in indexed_channel_ids if isinstance(self.cog.bot.get_channel(ch_id), discord.ForumChannel)]
        
        view = ChannelSelectionView(self.cog, interaction, channels)
        await self.cog.bot.api_scheduler.submit(
            coro=interaction.followup.send("请选择要搜索的频道：", view=view, ephemeral=True),
            priority=1
        )