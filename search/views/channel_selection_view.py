import discord
from typing import List, TYPE_CHECKING

from .generic_search_view import GenericSearchView

if TYPE_CHECKING:
    from ..cog import Search

class ChannelSelectionView(discord.ui.View):
    """第一步：让用户选择要搜索的频道。"""
    def __init__(self, cog: "Search", original_interaction: discord.Interaction, channels: List[discord.ForumChannel]):
        super().__init__(timeout=900)
        self.cog = cog
        self.original_interaction = original_interaction
        
        options = [discord.SelectOption(label="所有已索引频道", value="all")]
        options.extend([discord.SelectOption(label=ch.name, value=str(ch.id)) for ch in channels])
        
        self.channel_select = discord.ui.Select(
            placeholder="选择要搜索的频道...",
            options=options,
            min_values=1,
            max_values=len(options)
        )
        self.channel_select.callback = self.on_channel_select
        self.add_item(self.channel_select)

    async def on_channel_select(self, interaction: discord.Interaction):
        """处理频道选择，然后切换到通用的搜索条件视图。"""
        # 使用 defer 来防止 "Unknown Interaction"
        if not interaction.response.is_done():
            await self.cog.bot.api_scheduler.submit(
                coro=interaction.response.defer(),
                priority=1
            )
        
        all_channel_ids = [int(opt.value) for opt in self.channel_select.options if opt.value != 'all']
        
        if "all" in self.channel_select.values:
            selected_channel_ids = all_channel_ids
        else:
            selected_channel_ids = [int(v) for v in self.channel_select.values]

        # 切换到通用搜索视图
        generic_view = GenericSearchView(self.cog, self.original_interaction, selected_channel_ids)
        await generic_view.start()