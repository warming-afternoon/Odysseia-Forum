import discord
from typing import TYPE_CHECKING

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

        # 创建一个新的 GenericSearchView 实例并恢复其状态
        view = GenericSearchView(
            self.cog, self.original_interaction, self.state["channel_ids"]
        )

        # 恢复所有筛选条件
        view.include_tags = self.state.get("include_tags", set())
        view.exclude_tags = self.state.get("exclude_tags", set())
        view.author_ids = self.state.get("author_ids", set())
        view.keywords = self.state.get("keywords", "")
        view.exclude_keywords = self.state.get("exclude_keywords", "")
        view.tag_logic = self.state.get("tag_logic", "and")
        view.sort_method = self.state.get("sort_method", "comprehensive")
        view.sort_order = self.state.get("sort_order", "desc")

        # 使用恢复的状态更新视图
        await view.update_view(interaction, page=self.state.get("page", 1))
