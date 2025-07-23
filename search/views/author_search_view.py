import discord
from typing import List, TYPE_CHECKING

from shared.models.tag import Tag
from ..models.qo.thread_search import ThreadSearchQuery
from .results_view import NewSearchResultsView
from .components.search_button import SearchButton
from .components.keyword_button import KeywordButton
from .components.tag_logic_button import TagLogicButton
from .components.sort_order_button import SortOrderButton
from .components.sort_method_select import SortMethodSelect

if TYPE_CHECKING:
    from ..cog import Search

class NewAuthorTagSelectionView(discord.ui.View):
    def __init__(self, cog: "Search", interaction: discord.Interaction, author_id: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.original_interaction = interaction
        self.author_id = author_id
        
        self.all_tags: List[Tag] = []
        self.include_tags = set()
        self.exclude_tags = set()
        self.keywords = ""
        self.tag_logic = "and"
        self.sort_method = "comprehensive"
        self.sort_order = "desc"

    async def start(self):
        """初始化视图并发送第一条消息"""
        await self.update_view()

    async def update_view(self, interaction: discord.Interaction = None):
        """根据当前状态更新或编辑消息"""
        target_interaction = interaction or self.original_interaction
        if interaction:
            await self.cog.bot.api_scheduler.submit(
                coro=interaction.response.defer(),
                priority=1
            )
        
        self.all_tags = await self.cog.tag_system_repo.get_tags_for_author(self.author_id)
        
        self.clear_items()
        
        self.add_item(self.create_tag_select("正选", self.include_tags, "include_tags"))
        self.add_item(self.create_tag_select("反选", self.exclude_tags, "exclude_tags"))
        self.add_item(KeywordButton(self.update_view))
        self.add_item(TagLogicButton(self.tag_logic, self.update_view))
        self.add_item(SortOrderButton(self.sort_order, self.update_view))
        self.add_item(SortMethodSelect(self.sort_method, self.update_view))
        self.add_item(SearchButton(self.execute_search))

        content = f"正在为作者 <@{self.author_id}> 配置搜索条件..."
        
        if target_interaction.response.is_done():
            await self.cog.bot.api_scheduler.submit(
                coro=target_interaction.edit_original_response(content=content, view=self, embeds=[]),
                priority=1
            )
        else:
            await self.cog.bot.api_scheduler.submit(
                coro=target_interaction.response.send_message(content=content, view=self, ephemeral=True),
                priority=1
            )
            self.original_interaction = await self.cog.bot.api_scheduler.submit(
                coro=target_interaction.original_response(),
                priority=1
            )

    def create_tag_select(self, placeholder: str, selected_values: set, custom_id: str):
        options = [discord.SelectOption(label=tag.name, value=str(tag.id)) for tag in self.all_tags]
        select = discord.ui.Select(
            placeholder=f"选择要{placeholder}的标签",
            options=options if options else [discord.SelectOption(label="该作者没有标签", value="no_tags")],
            min_values=0,
            max_values=len(options) if options else 1,
            custom_id=custom_id,
            disabled=not options
        )
        for option in select.options:
            if option.value != "no_tags" and int(option.value) in selected_values:
                option.default = True
        
        async def select_callback(interaction: discord.Interaction):
            values = {int(v) for v in select.values if v != "no_tags"}
            if select.custom_id == "include_tags":
                self.include_tags = values
            else:
                self.exclude_tags = values
            await self.update_view(interaction)
            
        select.callback = select_callback
        return select

    async def execute_search(self, interaction: discord.Interaction):
        await self.cog.bot.api_scheduler.submit(
            coro=interaction.response.defer(),
            priority=1
        )

        indexed_channel_ids = await self.cog.tag_system_repo.get_indexed_channel_ids()

        qo = ThreadSearchQO(
            channel_ids=list(indexed_channel_ids),
            author_ids=[self.author_id],
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
            content = f"快捷搜索 - 作者 <@{self.author_id}>：\n\n🔍 **搜索结果：** 找到 {results['total']} 个帖子 (第{results['page']}/{results['max_page']}页)"
            await self.cog.bot.api_scheduler.submit(
                coro=interaction.edit_original_response(content=content, embeds=results['embeds'], view=view),
                priority=1
            )