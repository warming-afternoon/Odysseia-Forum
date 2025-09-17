import discord
from typing import TYPE_CHECKING

from ...dto.search_state import SearchStateDTO

if TYPE_CHECKING:
    from ...cog import Search


class ContinueButton(discord.ui.Button):
    """用于从超时状态恢复视图的按钮。"""

    def __init__(
        self, cog: "Search", original_interaction: discord.Interaction, state: dict
    ):
        super().__init__(label="🔄 继续搜索", style=discord.ButtonStyle.primary)
        self.cog = cog
        self.original_interaction = original_interaction
        self.state = state

    async def callback(self, interaction: discord.Interaction):
        # 懒加载以避免循环导入
        from ..generic_search_view import GenericSearchView

        # 从 state 字典重建 SearchStateDTO
        search_state = SearchStateDTO(**self.state)

        # 创建一个新的 GenericSearchView 实例并传入恢复的状态
        view = GenericSearchView(
            cog=self.cog,
            interaction=interaction,
            search_state=search_state,
        )

        # 使用恢复的状态更新视图。
        await view.update_view(interaction, rerun_search=True)
