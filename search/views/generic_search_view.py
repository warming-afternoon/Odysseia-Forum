import discord
from typing import List, TYPE_CHECKING

from shared.models.tag import Tag
from ..models.qo.thread_search import ThreadSearchQO
from .results_view import NewSearchResultsView
from .components.search_button import SearchButton
from .components.keyword_button import KeywordButton
from .components.tag_logic_button import TagLogicButton
from .components.sort_order_button import SortOrderButton
from .components.sort_method_select import SortMethodSelect

if TYPE_CHECKING:
    from ..cog import Search

class GenericSearchView(discord.ui.View):
    """一个通用的搜索条件选择视图，用于全局搜索等场景。"""

    def __init__(self, cog: "Search", interaction: discord.Interaction, channel_ids: List[int]):
        super().__init__(timeout=900)  # 15分钟超时
        self.cog = cog
        self.original_interaction = interaction
        self.channel_ids = channel_ids
        
        # --- 搜索参数 ---
        self.all_tags: List[Tag] = []
        self.include_tags = set()
        self.exclude_tags = set()
        self.author_ids = set()
        self.keywords = ""
        self.tag_logic = "and"
        self.sort_method = "comprehensive"
        self.sort_order = "desc"

    async def start(self):
        """初始化视图并发送或编辑消息"""
        await self.update_view()

    async def update_view(self, interaction: discord.Interaction = None):
        """根据当前状态更新整个视图，包括UI组件和文本提示。"""
        target_interaction = interaction or self.original_interaction
        if interaction:
            # 使用调度器以高优先级执行defer
            await self.cog.bot.api_scheduler.submit(
                coro=interaction.response.defer(),
                priority=1
            )
        
        self.all_tags = await self.cog.tag_system_repo.get_tags_for_channels(self.channel_ids)
        
        self.clear_items()
        
        # --- 添加UI组件 ---
        # 第0行: 标签选择
        self.add_item(self.create_tag_select("正选", self.include_tags, "generic_include_tags", 0))
        self.add_item(self.create_tag_select("反选", self.exclude_tags, "generic_exclude_tags", 1))
        
        # 第2行: 作者选择
        self.add_item(AuthorSelect(self.on_author_select))
        
        # 第3行: 功能按钮
        self.add_item(KeywordButton(self.update_view))
        self.add_item(TagLogicButton(self.tag_logic, self.update_view))
        self.add_item(SortOrderButton(self.sort_order, self.update_view))

        # 第4行: 排序方式和执行按钮
        self.add_item(SortMethodSelect(self.sort_method, self.update_view))
        self.add_item(SearchButton(self.execute_search))

        content = self.build_content_string()
        
        # 通过调度器以高优先级更新消息
        edit_coro = target_interaction.edit_original_response(content=content, view=self, embeds=[])
        await self.cog.bot.api_scheduler.submit(coro=edit_coro, priority=1)

    def create_tag_select(self, placeholder: str, selected_values: set, custom_id: str, row: int):
        """创建一个标签选择的下拉菜单。"""
        options = [discord.SelectOption(label=tag.name, value=str(tag.id)) for tag in self.all_tags]
        select = discord.ui.Select(
            placeholder=f"选择要{placeholder}的标签",
            options=options if options else [discord.SelectOption(label="无可用标签", value="no_tags")],
            min_values=0, max_values=len(options) if options else 1,
            custom_id=custom_id, disabled=not options, row=row
        )
        # 保持选中状态
        for option in select.options:
            if option.value != "no_tags" and int(option.value) in selected_values:
                option.default = True
        
        async def select_callback(interaction: discord.Interaction):
            values = {int(v) for v in select.values if v != "no_tags"}
            if "include" in custom_id:
                self.include_tags = values
            else:
                self.exclude_tags = values
            await self.update_view(interaction)
            
        select.callback = select_callback
        return select

    async def on_author_select(self, interaction: discord.Interaction, users: List[discord.User]):
        """处理作者选择的回调。"""
        self.author_ids = {user.id for user in users}
        await self.update_view(interaction)

    def build_content_string(self) -> str:
        """构建并返回显示当前所有筛选条件的文本。"""
        parts = ["**全局搜索配置**\n"]
        if self.include_tags:
            names = [tag.name for tag in self.all_tags if tag.id in self.include_tags]
            parts.append(f"**包含标签:** {', '.join(names)}")
        if self.exclude_tags:
            names = [tag.name for tag in self.all_tags if tag.id in self.exclude_tags]
            parts.append(f"**排除标签:** {', '.join(names)}")
        if self.author_ids:
            parts.append(f"**指定作者:** {', '.join([f'<@{uid}>' for uid in self.author_ids])}")
        if self.keywords:
            parts.append(f"**关键词:** {self.keywords}")
        
        if len(parts) == 1:
            parts.append("当前无任何筛选条件。")
            
        return "\n".join(parts)

    async def execute_search(self, interaction: discord.Interaction):
        """收集所有参数，执行搜索，并显示结果。"""
        await self.cog.bot.api_scheduler.submit(
            coro=interaction.response.defer(),
            priority=1
        )

        qo = ThreadSearchQO(
            channel_ids=self.channel_ids,
            author_ids=list(self.author_ids) if self.author_ids else None,
            include_tag_ids=list(self.include_tags),
            exclude_tag_ids=list(self.exclude_tags),
            keywords=self.keywords,
            tag_logic=self.tag_logic,
            sort_by=self.sort_method,
            sort_order=self.sort_order
        )

        results = await self.cog._search_and_display(interaction, qo)

        if not results.get('has_results'):
            await self.cog.bot.api_scheduler.submit(
                coro=interaction.edit_original_response(content="没有找到符合条件的结果。", view=None, embeds=[]),
                priority=1
            )
        else:
            view = NewSearchResultsView(
                self.cog, interaction, qo,
                results['total'], results['page'], results['per_page']
            )
            content = f"全局搜索结果：\n\n🔍 **搜索结果：** 找到 {results['total']} 个帖子 (第{results['page']}/{results['max_page']}页)"
            await self.cog.bot.api_scheduler.submit(
                coro=interaction.edit_original_response(content=content, embeds=results['embeds'], view=view),
                priority=1
            )


class AuthorSelect(discord.ui.UserSelect):
    """作者选择下拉菜单组件。"""
    def __init__(self, callback):
        super().__init__(placeholder="筛选作者 (可选)", min_values=0, max_values=25, row=2)
        self.select_callback = callback

    async def callback(self, interaction: discord.Interaction):
        await self.select_callback(interaction, self.values)