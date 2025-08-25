import discord
from typing import List, TYPE_CHECKING, Optional

from search.dto.user_search_preferences import UserSearchPreferencesDTO
from shared.safe_defer import safe_defer
from ..qo.thread_search import ThreadSearchQuery
from .results_view import NewSearchResultsView
from .components.keyword_button import KeywordButton, KeywordModal
from .components.tag_logic_button import TagLogicButton
from .components.sort_order_button import SortOrderButton
from .components.sort_method_select import SortMethodSelect
from .timeout_view import TimeoutView
from .combined_search_view import CombinedSearchView
from .components.tag_page_button import TagPageButton

if TYPE_CHECKING:
    from ..cog import Search


class GenericSearchView(discord.ui.View):
    """筛选逻辑的核心视图，管理所有搜索参数和UI状态。"""

    def __init__(
        self,
        cog: "Search",
        interaction: discord.Interaction,
        channel_ids: List[int],
        user_prefs: Optional[UserSearchPreferencesDTO] = None,
    ):
        super().__init__(timeout=900)
        self.cog = cog
        self.original_interaction = interaction
        self.last_interaction = interaction
        self.channel_ids = channel_ids

        # --- 搜索参数 ---
        # 根据所选频道获取合并后的标签
        merged_tags = self.cog.get_merged_tags(self.channel_ids)
        self.all_unique_tags: List[str] = [tag.name for tag in merged_tags]

        # 从用户偏好加载初始值，否则使用默认值
        if user_prefs:
            self.include_tags: set[str] = set(user_prefs.include_tags or [])
            self.exclude_tags: set[str] = set(user_prefs.exclude_tags or [])
            # 注意：快捷搜索的作者ID不应被偏好覆盖，所以这里不加载作者偏好
            self.author_ids: set[int] = set()
            self.keywords = user_prefs.include_keywords or ""
            self.exclude_keywords = user_prefs.exclude_keywords or ""
            self.exemption_markers: List[str] = (
                user_prefs.exclude_keyword_exemption_markers
                if user_prefs.exclude_keyword_exemption_markers is not None
                else ["禁", "🈲"]
            )
        else:
            self.include_tags: set[str] = set()
            self.exclude_tags: set[str] = set()
            self.author_ids: set[int] = set()
            self.keywords = ""
            self.exclude_keywords = ""
            self.exemption_markers: List[str] = ["禁", "🈲"]

        self.tag_logic = "or"
        self.sort_method = "comprehensive"
        self.sort_order = "desc"

        # --- UI状态 ---
        self.page = 1
        self.tag_page = 0
        self.tags_per_page = 25
        self.last_search_results: dict | None = None

    async def start(self, send_new_ephemeral: bool = False):
        """
        初始化视图
        :param send_new_ephemeral: 如果为 True，则发送一个新的私密消息，而不是编辑原始消息。
        """
        await self.update_view(
            self.original_interaction, send_new_ephemeral=send_new_ephemeral
        )

    def get_filter_components(self) -> List[discord.ui.Item]:
        """准备所有筛选UI组件的列表，但不添加到视图中。"""
        components = []

        # 第 0 行: 正选标签
        components.append(
            self.create_tag_select("正选", self.include_tags, "generic_include_tags", 0)
        )

        # 第 1 行: 反选标签
        components.append(
            self.create_tag_select("反选", self.exclude_tags, "generic_exclude_tags", 1)
        )

        # 第 2 行: 控制按钮
        components.append(KeywordButton(self.show_keyword_modal, row=2))

        if len(self.all_unique_tags) > self.tags_per_page:
            max_page = (len(self.all_unique_tags) - 1) // self.tags_per_page
            components.append(
                TagPageButton(
                    "prev", self.on_tag_page_change, row=2, disabled=self.tag_page == 0
                )
            )

        components.append(
            TagLogicButton(self.tag_logic, self.on_tag_logic_change, row=2)
        )

        if len(self.all_unique_tags) > self.tags_per_page:
            max_page = (len(self.all_unique_tags) - 1) // self.tags_per_page
            components.append(
                TagPageButton(
                    "next",
                    self.on_tag_page_change,
                    row=2,
                    disabled=self.tag_page >= max_page,
                )
            )

        components.append(
            SortOrderButton(self.sort_order, self.on_sort_order_change, row=2)
        )

        # 第 3 行: 排序选择器
        components.append(
            SortMethodSelect(self.sort_method, self.on_sort_method_change, row=3)
        )

        return components

    async def update_view(
        self,
        interaction: discord.Interaction,
        page: int = 1,
        rerun_search: bool = True,
        send_new_ephemeral: bool = False,
    ):
        """
        根据当前状态更新整个视图，包括UI组件和搜索结果
        :param send_new_ephemeral: 如果为 True，则发送一个新的私密消息，而不是编辑原始消息。
        """
        self.last_interaction = interaction
        await safe_defer(interaction, ephemeral=True)
        self.page = page

        results = {}
        if rerun_search:
            qo = self.build_query_object()
            results = await self.cog._search_and_display(interaction, qo, self.page)
            self.last_search_results = results
        elif self.last_search_results:
            results = self.last_search_results
        else:
            # Fallback: if no cache exists, must run search
            qo = self.build_query_object()
            results = await self.cog._search_and_display(interaction, qo, self.page)
            self.last_search_results = results

        # 准备所有UI组件
        filter_components = self.get_filter_components()

        # 构建消息和最终视图
        content = "搜索结果"
        if self.channel_ids:
            guild = interaction.guild
            if guild:
                channels = [guild.get_channel(cid) for cid in self.channel_ids]
                channel_mentions = [ch.mention for ch in channels if ch]
                if channel_mentions:
                    content = f"在 {', '.join(channel_mentions)} 中搜索"
        thread_embeds = results.get("embeds", [])
        summary_embed = self.build_summary_embed(results)

        final_embeds_to_send = thread_embeds + [summary_embed]

        final_view = None
        if results.get("has_results"):
            # 如果有结果，创建分页视图并组合
            results_view = NewSearchResultsView(
                self.cog,
                interaction,
                self.build_query_object(),
                results["total"],
                results["page"],
                results["per_page"],
                self.update_view,
            )
            final_view = CombinedSearchView(self, results_view, filter_components)
        else:
            # 如果没有结果，只显示筛选器。
            # 重新创建一个只包含筛选组件的视图，避免状态污染。
            final_view = discord.ui.View(timeout=self.timeout)
            for item in filter_components:
                final_view.add_item(item)

        # 更新消息
        # 根据模式选择是编辑还是发送新消息
        if send_new_ephemeral:
            send_coro = interaction.followup.send(
                content=content,
                view=final_view,
                embeds=final_embeds_to_send,
                ephemeral=True,
            )
            await self.cog.bot.api_scheduler.submit(coro_factory=lambda: send_coro, priority=1)
        else:
            edit_coro = interaction.edit_original_response(
                content=content, view=final_view, embeds=final_embeds_to_send
            )
            await self.cog.bot.api_scheduler.submit(coro_factory=lambda: edit_coro, priority=1)

    async def on_filter_change(self, interaction: discord.Interaction):
        """当任何筛选条件改变时调用此方法"""
        self.last_interaction = interaction
        await self.update_view(interaction, page=1)

    async def on_sort_order_change(self, interaction: discord.Interaction):
        """处理排序顺序改变的逻辑"""
        self.last_interaction = interaction
        self.sort_order = "asc" if self.sort_order == "desc" else "desc"
        await self.on_filter_change(interaction)

    async def on_tag_logic_change(self, interaction: discord.Interaction):
        """处理标签匹配逻辑改变的逻辑"""
        self.last_interaction = interaction
        self.tag_logic = "or" if self.tag_logic == "and" else "and"
        await self.on_filter_change(interaction)

    async def on_sort_method_change(
        self, interaction: discord.Interaction, new_method: str
    ):
        """处理排序方法改变的逻辑"""
        self.last_interaction = interaction
        self.sort_method = new_method
        await self.on_filter_change(interaction)

    async def on_tag_page_change(self, interaction: discord.Interaction, action: str):
        """处理标签翻页"""
        self.last_interaction = interaction
        max_page = (len(self.all_unique_tags) - 1) // self.tags_per_page
        if action == "prev":
            self.tag_page = max(0, self.tag_page - 1)
        elif action == "next":
            self.tag_page = min(max_page, self.tag_page + 1)

        # 翻页后，只需更新视图，不需要重新搜索
        await self.update_view(interaction, self.page, rerun_search=False)

    async def handle_keyword_update(
        self,
        interaction: discord.Interaction,
        keywords: str,
        exclude_keywords: str,
        exemption_markers: str,
    ):
        """处理来自KeywordModal的数据回传"""
        import re

        self.last_interaction = interaction
        self.keywords = keywords
        self.exclude_keywords = exclude_keywords
        self.exemption_markers = sorted(
            list({p.strip() for p in re.split(r"[，,/\s]+", exemption_markers) if p.strip()})
        )
        await self.update_view(interaction, page=1)

    async def show_keyword_modal(self, interaction: discord.Interaction):
        """创建并显示关键词模态框"""
        self.last_interaction = interaction
        modal = KeywordModal(
            initial_keywords=self.keywords,
            initial_exclude_keywords=self.exclude_keywords,
            initial_exemption_markers=", ".join(self.exemption_markers),
            submit_callback=self.handle_keyword_update,
        )
        await interaction.response.send_modal(modal)

    def create_tag_select(
        self,
        placeholder_prefix: str,
        selected_values: set[str],
        custom_id: str,
        row: int,
    ):
        """创建一个支持分页的、按名称选择的标签下拉菜单。"""
        start_idx = self.tag_page * self.tags_per_page
        end_idx = start_idx + self.tags_per_page
        current_page_tags = self.all_unique_tags[start_idx:end_idx]

        options = [
            discord.SelectOption(label=tag_name, value=tag_name)
            for tag_name in current_page_tags
        ]

        # 根据已选中的值动态生成 placeholder
        if selected_values:
            placeholder_text = f"已{placeholder_prefix}: " + ", ".join(
                sorted(list(selected_values))
            )
            if len(placeholder_text) > 100:
                placeholder_text = placeholder_text[:97] + "..."
        else:
            placeholder_text = (
                f"选择要{placeholder_prefix}的标签 (第 {self.tag_page + 1} 页)"
            )

        select = discord.ui.Select(
            placeholder=placeholder_text,
            options=options
            if options
            else [discord.SelectOption(label="无可用标签", value="no_tags")],
            min_values=0,
            max_values=len(options) if options else 1,
            custom_id=custom_id,
            disabled=not options,
            row=row,
        )

        # 设置默认选中的选项
        for option in select.options:
            if option.value in selected_values:
                option.default = True

        async def select_callback(interaction: discord.Interaction):
            self.last_interaction = interaction

            current_page_tag_names = {
                opt.value for opt in options if opt.value != "no_tags"
            }

            # 找出在其他页面上已经选择的标签
            tags_on_other_pages = {
                tag_name
                for tag_name in selected_values
                if tag_name not in current_page_tag_names
            }

            # 获取当前页面的新选择
            new_selections = {v for v in select.values if v != "no_tags"}

            # 合并其他页面的选择和当前页面的新选择
            final_selection = tags_on_other_pages.union(new_selections)

            if "include" in custom_id:
                self.include_tags = final_selection
            else:
                self.exclude_tags = final_selection

            await self.update_view(interaction, page=1)

        select.callback = select_callback
        return select

    async def on_author_select(
        self, interaction: discord.Interaction, users: List[discord.User]
    ):
        """处理作者选择的回调"""
        self.last_interaction = interaction
        self.author_ids = {user.id for user in users}
        await self.update_view(interaction, page=1)

    def build_query_object(self) -> ThreadSearchQuery:
        """根据当前视图状态构建查询对象。"""
        return ThreadSearchQuery(
            channel_ids=self.channel_ids,
            include_authors=list(self.author_ids) if self.author_ids else None,
            include_tags=list(self.include_tags),
            exclude_tags=list(self.exclude_tags),
            keywords=self.keywords,
            exclude_keywords=self.exclude_keywords,
            exclude_keyword_exemption_markers=self.exemption_markers,
            tag_logic=self.tag_logic,
            sort_method=self.sort_method,
            sort_order=self.sort_order,
        )

    def build_summary_embed(self, results: dict) -> discord.Embed:
        """构建并返回一个包含当前筛选条件和结果摘要的Embed"""
        description_parts = []
        filters = []
        if self.include_tags:
            filters.append(f"含: {', '.join(sorted(list(self.include_tags)))}")
        if self.exclude_tags:
            filters.append(f"不含: {', '.join(sorted(list(self.exclude_tags)))}")
        if self.author_ids:
            filters.append(
                f"作者: {', '.join([f'<@{uid}>' for uid in self.author_ids])}"
            )
        if self.keywords:
            filters.append(f"包含关键词: {self.keywords}")
        if self.exclude_keywords:
            filters.append(f"排除关键词: {self.exclude_keywords}")
        # if self.exemption_markers:
        #     filters.append(f"豁免标记: {', '.join(self.exemption_markers)}")

        if filters:
            description_parts.append("\n".join(filters))

        if results.get("has_results"):
            summary = f"🔍 找到 {results['total']} 个帖子 (第{results['page']}/{results['max_page']}页)"
            color = discord.Color.green()
        else:
            summary = results.get("error", "没有找到符合条件的结果。")
            color = discord.Color.orange()

        description_parts.append(summary)

        embed = discord.Embed(description="\n".join(description_parts), color=color)
        return embed

    async def on_timeout(self):
        """当视图超时时，保存状态并显示一个带有“继续”按钮的新视图"""
        state = {
            "channel_ids": self.channel_ids,
            "include_tags": list(self.include_tags),
            "exclude_tags": list(self.exclude_tags),
            "author_ids": list(self.author_ids),
            "keywords": self.keywords,
            "exclude_keywords": self.exclude_keywords,
            "exemption_markers": self.exemption_markers,
            "tag_logic": self.tag_logic,
            "sort_method": self.sort_method,
            "sort_order": self.sort_order,
            "page": self.page,
            "tag_page": self.tag_page,
        }

        timeout_view = TimeoutView(self.cog, self.original_interaction, state)

        try:
            if not self.last_interaction:
                return

            edit_coro = self.last_interaction.edit_original_response(
                content="⏰ 搜索界面已超时，点击下方按钮可恢复之前的搜索状态。",
                view=timeout_view,
                embeds=[],
            )
            await self.cog.bot.api_scheduler.submit(coro_factory=lambda: edit_coro, priority=1)
        except (discord.errors.NotFound, discord.errors.HTTPException):
            pass
