import discord
from typing import List, TYPE_CHECKING

from shared.discord_utils import safe_defer
from .generic_search_view import GenericSearchView

if TYPE_CHECKING:
    from ..cog import Search


class ChannelSelectionView(discord.ui.View):
    """第一步：让用户选择要搜索的频道（支持多选）。"""

    def __init__(
        self,
        cog: "Search",
        original_interaction: discord.Interaction,
        channels: List[discord.ForumChannel],
        all_channel_ids: List[int],
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.original_interaction = original_interaction
        self.channels = channels
        self.all_channel_ids = all_channel_ids
        self.selected_channel_ids: List[int] = []

        # 构建选项
        options = [discord.SelectOption(label="所有已索引频道", value="all")]
        # Discord限制25个选项，为"all"选项留一个位置
        options.extend(
            [
                discord.SelectOption(label=ch.name, value=str(ch.id))
                for ch in channels[:24]
            ]
        )

        self.channel_select = discord.ui.Select(
            placeholder="选择论坛频道（可多选）...",
            options=options,
            min_values=1,
            max_values=len(options),
        )
        self.channel_select.callback = self.on_channel_select
        self.add_item(self.channel_select)

        self.confirm_button = discord.ui.Button(
            label="✅ 确定搜索",
            style=discord.ButtonStyle.success,
            disabled=True,  # 初始为禁用
        )
        self.confirm_button.callback = self.on_confirm
        self.add_item(self.confirm_button)

    async def on_channel_select(self, interaction: discord.Interaction):
        """当用户在下拉菜单中做出选择时调用。"""
        # 启用确定按钮
        self.confirm_button.disabled = False

        selected_values = self.channel_select.values

        # 更新选项的默认状态以保持选择
        for option in self.channel_select.options:
            option.default = option.value in selected_values

        # 更新消息以反映当前选择
        if "all" in selected_values:
            display_text = "所有已索引频道"
        else:
            # 从 self.channels 中查找名称
            selected_names = [
                ch.name for ch in self.channels if str(ch.id) in selected_values
            ]
            display_text = ", ".join(selected_names)

        await interaction.response.edit_message(
            content=f"**已选择:** {display_text}\n\n请点击“确定搜索”继续。", view=self
        )

    async def on_confirm(self, interaction: discord.Interaction):
        """当用户点击“确定”按钮后，切换到通用的搜索视图。"""
        await safe_defer(interaction)

        selected_values = self.channel_select.values

        if "all" in selected_values:
            # 如果选择了 "all"，则使用所有可用的频道ID
            self.selected_channel_ids = self.all_channel_ids
        else:
            self.selected_channel_ids = [int(v) for v in selected_values]

        if not self.selected_channel_ids:
            await interaction.followup.send("请至少选择一个频道。", ephemeral=True)
            return

        # 切换到通用搜索视图
        generic_view = GenericSearchView(
            self.cog, interaction, self.selected_channel_ids
        )
        await generic_view.start()
