import discord
import re

from typing import List, TYPE_CHECKING, Set, Optional

from search.dto.search_state import SearchStateDTO
from shared.safe_defer import safe_defer
from shared.view.tag_select import TagSelect
from ..qo.thread_search import ThreadSearchQuery
from .results_view import SearchResultsView
from .components.keyword_button import KeywordButton, KeywordModal
from .components.tag_logic_button import TagLogicButton
from .components.sort_order_button import SortOrderButton
from .components.sort_method_select import SortMethodSelect
from .timeout_view import TimeoutView
from .combined_search_view import CombinedSearchView
from .components.tag_page_button import TagPageButton
from .custom_search_settings_view import CustomSearchSettingsView
from ..dto.search_state import SearchStateDTO
from src.shared.default_preferences import DefaultPreferences
from src.search.constants import SortMethod

if TYPE_CHECKING:
    from ..cog import Search


class GenericSearchView(discord.ui.View):
    """筛选逻辑的核心视图，它操作一个 SearchStateDTO 来管理状态。"""

    def __init__(
        self,
        cog: "Search",
        interaction: discord.Interaction,
        search_state: SearchStateDTO,
    ):
        super().__init__(timeout=885)
        self.cog = cog
        self.original_interaction = interaction
        self.last_interaction = interaction
        self.search_state = search_state

        # --- UI状态 ---
        self.tags_per_page = 25
        self.last_search_results: dict | None = None
        self.custom_settings_message: Optional[discord.WebhookMessage] = None

    async def start(self, send_new_ephemeral: bool = False):
        """
        初始化视图

        Args:
            send_new_ephemeral (bool): 如果为 True，则发送一个新的私密消息，而不是编辑原始消息
        """
        await self.update_view(
            self.original_interaction, send_new_ephemeral=send_new_ephemeral
        )

    def get_filter_components(self) -> List[discord.ui.Item]:
        """准备所有筛选UI组件的列表，但不添加到视图中。"""
        components = []
        state = self.search_state
        all_tags = state.all_available_tags

        # 第 0 行: 正选标签
        components.append(
            TagSelect(
                all_tags=all_tags,
                selected_tags=state.include_tags,
                page=state.tag_page,
                tags_per_page=self.tags_per_page,
                placeholder_prefix="正选",
                custom_id="generic_include_tags",
                on_change_callback=self.on_include_tags_change,
                row=0,
            )
        )

        # 第 1 行: 反选标签
        components.append(
            TagSelect(
                all_tags=all_tags,
                selected_tags=state.exclude_tags,
                page=state.tag_page,
                tags_per_page=self.tags_per_page,
                placeholder_prefix="反选",
                custom_id="generic_exclude_tags",
                on_change_callback=self.on_exclude_tags_change,
                row=1,
            )
        )

        # 第 2 行: 控制按钮
        components.append(KeywordButton(self.show_keyword_modal, row=2))

        if len(all_tags) > self.tags_per_page:
            max_page = (len(all_tags) - 1) // self.tags_per_page
            components.append(
                TagPageButton(
                    "prev", self.on_tag_page_change, row=2, disabled=state.tag_page == 0
                )
            )

        components.append(
            TagLogicButton(state.tag_logic, self.on_tag_logic_change, row=2)
        )

        if len(all_tags) > self.tags_per_page:
            max_page = (len(all_tags) - 1) // self.tags_per_page
            components.append(
                TagPageButton(
                    "next",
                    self.on_tag_page_change,
                    row=2,
                    disabled=state.tag_page >= max_page,
                )
            )

        components.append(
            SortOrderButton(state.sort_order, self.on_sort_order_change, row=2)
        )

        # 第 3 行: 排序选择器
        sort_select = SortMethodSelect(state.sort_method, self.on_sort_method_change, row=3)

        # 动态修改自定义搜索的标签
        if state.sort_method == "custom":
            # 找到 "自定义搜索" 对应的选项
            custom_option = next((opt for opt in sort_select.options if opt.value == "custom"), None)
            
            if custom_option:
                # 获取基础排序算法的显示名称
                base_sort_label = SortMethod.get_short_label_by_value(state.custom_base_sort)
                
                # 更新标签
                custom_option.label = f"🛠️ 自定义 ({base_sort_label})"

        components.append(sort_select)

        return components

    async def update_view(
        self,
        interaction: discord.Interaction,
        rerun_search: bool = True,
        send_new_ephemeral: bool = False,
    ):
        """
        根据当前状态更新整个视图，包括UI组件和搜索结果

        Args:
            rerun_search (bool): 如果为 True，则根据恢复的状态重新执行一次搜索
            send_new_ephemeral (bool): 如果为 True，则发送一个新的私密消息，而不是编辑原始消息

        """
        self.last_interaction = interaction
        await safe_defer(interaction, ephemeral=True)

        results = {}
        if rerun_search:
            results = await self._execute_search(interaction)
            self.last_search_results = results
        elif self.last_search_results:
            results = self.last_search_results
        else:
            # Fallback: if no cache exists, must run search
            results = await self._execute_search(interaction)
            self.last_search_results = results

        # 准备所有UI组件
        filter_components = self.get_filter_components()

        # 构建消息和最终视图
        content = "搜索结果"
        if self.search_state.channel_ids:
            guild = interaction.guild
            if guild:
                channels = [
                    guild.get_channel(cid) for cid in self.search_state.channel_ids
                ]
                channel_mentions = [ch.mention for ch in channels if ch]
                if channel_mentions:
                    content = f"在 {', '.join(channel_mentions)} 中搜索"
        thread_embeds = results.get("embeds", [])
        summary_embed = self.build_summary_embed(results)

        final_embeds_to_send = thread_embeds + [summary_embed]

        final_view = None
        if results.get("has_results"):
            # 如果有结果，创建分页视图并组合
            results_view = SearchResultsView(
                self.cog,
                interaction,
                self.build_query_object(),
                results["total"],
                results["page"],
                results["per_page"],
                self.update_view_from_pager,  # 使用新的回调
                self.search_state.results_per_page,
                self.search_state.preview_image_mode,
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
            await self.cog.bot.api_scheduler.submit(
                coro_factory=lambda: send_coro, priority=1
            )
        else:
            edit_coro = interaction.edit_original_response(
                content=content, view=final_view, embeds=final_embeds_to_send
            )
            await self.cog.bot.api_scheduler.submit(
                coro_factory=lambda: edit_coro, priority=1
            )

    async def _execute_search(self, interaction: discord.Interaction) -> dict:
        """执行搜索并返回结果"""
        state = self.search_state
        search_qo = self.build_query_object()

        # 从 self.search_state 中获取显示参数
        results = await self.cog._search_and_display(
            interaction=interaction,
            search_qo=search_qo,
            page=state.page,
            per_page=state.results_per_page,  # 传递每页数量
            preview_mode=state.preview_image_mode,  # 传递预览模式
        )
        return results

    async def update_view_from_pager(
        self,
        interaction: discord.Interaction,
        page: int,
        per_page: int,
        preview_mode: str,
    ):
        """由分页视图 (SearchResultsView) 调用的回调"""
        self.search_state.page = page
        self.search_state.results_per_page = per_page
        self.search_state.preview_image_mode = preview_mode
        await self.update_view(interaction, rerun_search=True)

    async def on_filter_change(self, interaction: discord.Interaction):
        """当任何筛选条件改变时调用此方法，重置页码并重新搜索"""
        self.last_interaction = interaction
        self.search_state.page = 1
        await self.update_view(interaction, rerun_search=True)

    async def on_sort_order_change(self, interaction: discord.Interaction):
        """处理排序顺序改变的逻辑"""
        self.search_state.sort_order = (
            "asc" if self.search_state.sort_order == "desc" else "desc"
        )
        await self.on_filter_change(interaction)

    async def on_tag_logic_change(self, interaction: discord.Interaction):
        """处理标签匹配逻辑改变的逻辑"""
        self.search_state.tag_logic = (
            "or" if self.search_state.tag_logic == "and" else "and"
        )
        await self.on_filter_change(interaction)

    async def on_sort_method_change(
        self, interaction: discord.Interaction, new_method: str
    ):
        """处理排序方法改变的逻辑"""
        # 清理可能存在的旧配置视图
        if self.custom_settings_message:
            try:
                await self.custom_settings_message.delete()
            except (discord.errors.NotFound, discord.errors.HTTPException):
                pass  # 消息可能已被删除，忽略错误
            finally:
                self.custom_settings_message = None

        if new_method == "custom":
            self.search_state.sort_method = "custom"
            await safe_defer(interaction, ephemeral=True)
            settings_view = CustomSearchSettingsView(self)
            # 启动并保存配置视图的消息
            self.custom_settings_message = await settings_view.start()
            # 打开配置视图时不立即搜索，等待用户操作
            return
            
        self.search_state.sort_method = new_method
        await self.on_filter_change(interaction)

    async def trigger_search_from_custom_settings(self, updated_state: "SearchStateDTO"):
        """由 CustomSearchSettingsView 回调，应用设置并使用 self.last_interaction 刷新主视图"""
        self.search_state = updated_state
        await self.on_filter_change(self.last_interaction)

    async def on_tag_page_change(self, interaction: discord.Interaction, action: str):
        """处理标签翻页"""
        max_page = (len(self.search_state.all_available_tags) - 1) // self.tags_per_page
        if action == "prev":
            self.search_state.tag_page = max(0, self.search_state.tag_page - 1)
        elif action == "next":
            self.search_state.tag_page = min(max_page, self.search_state.tag_page + 1)

        # 翻页后，只需更新视图，不需要重新搜索
        await self.update_view(interaction, rerun_search=False)

    async def handle_keyword_update(
        self,
        interaction: discord.Interaction,
        keywords: str,
        exclude_keywords: str,
        exemption_markers: str,
    ):
        """处理来自KeywordModal的数据回传"""

        self.search_state.keywords = keywords
        self.search_state.exclude_keywords = exclude_keywords
        self.search_state.exemption_markers = sorted(
            list(
                {
                    p.strip()
                    for p in re.split(r"[，,/\s]+", exemption_markers)
                    if p.strip()
                }
            )
        )
        await self.on_filter_change(interaction)

    async def show_keyword_modal(self, interaction: discord.Interaction):
        """创建并显示关键词模态框"""
        modal = KeywordModal(
            initial_keywords=self.search_state.keywords,
            initial_exclude_keywords=self.search_state.exclude_keywords,
            initial_exemption_markers=", ".join(self.search_state.exemption_markers),
            submit_callback=self.handle_keyword_update,
        )
        await interaction.response.send_modal(modal)

    async def on_include_tags_change(
        self, interaction: discord.Interaction, new_selection: Set[str]
    ):
        self.search_state.include_tags = new_selection
        await self.on_filter_change(interaction)

    async def on_exclude_tags_change(
        self, interaction: discord.Interaction, new_selection: Set[str]
    ):
        self.search_state.exclude_tags = new_selection
        await self.on_filter_change(interaction)

    def build_query_object(self) -> ThreadSearchQuery:
        """根据当前视图状态构建查询对象。"""
        state = self.search_state
        return ThreadSearchQuery(
            channel_ids=state.channel_ids,
            include_authors=list(state.include_authors)
            if state.include_authors
            else None,
            exclude_authors=list(state.exclude_authors)
            if state.exclude_authors
            else None,
            include_tags=list(state.include_tags),
            exclude_tags=list(state.exclude_tags),
            keywords=state.keywords,
            exclude_keywords=state.exclude_keywords,
            exclude_keyword_exemption_markers=state.exemption_markers,
            tag_logic=state.tag_logic,
            sort_method=state.sort_method,
            sort_order=state.sort_order,
            custom_base_sort=state.custom_base_sort,
            reaction_count_range=state.reaction_count_range,
            reply_count_range=state.reply_count_range,
            created_after=state.created_after,
            created_before=state.created_before,
            active_after=state.active_after,
            active_before=state.active_before,
        )

    def build_summary_embed(self, results: dict) -> discord.Embed:
        """构建并返回一个包含当前筛选条件和结果摘要的Embed"""
        description_parts = []
        filters = []
        state = self.search_state

        if state.include_tags:
            filters.append(f"含: {', '.join(sorted(list(state.include_tags)))}")
        if state.exclude_tags:
            filters.append(f"不含: {', '.join(sorted(list(state.exclude_tags)))}")
        if state.include_authors:
            filters.append(
                f"只看作者: {', '.join([f'<@{uid}>' for uid in state.include_authors])}"
            )
        if state.exclude_authors:
            filters.append(
                f"屏蔽作者: {', '.join([f'<@{uid}>' for uid in state.exclude_authors])}"
            )
        if state.keywords:
            filters.append(f"包含关键词: {state.keywords}")
        if state.exclude_keywords:
            filters.append(f"排除关键词: {state.exclude_keywords}")

        if state.sort_method == "custom":
            custom_filters = []
            # 数值范围
            if state.reaction_count_range != DefaultPreferences.DEFAULT_NUMERIC_RANGE.value:
                custom_filters.append(f"反应数: `{state.reaction_count_range}`")
            if state.reply_count_range != DefaultPreferences.DEFAULT_NUMERIC_RANGE.value:
                custom_filters.append(f"回复数: `{state.reply_count_range}`")
            
            # 时间范围
            if state.created_after: custom_filters.append(f"发帖晚于 {state.created_after.strftime('%Y-%m-%d')}")
            if state.created_before: custom_filters.append(f"发帖早于 {state.created_before.strftime('%Y-%m-%d')}")
            if state.active_after: custom_filters.append(f"活跃晚于 {state.active_after.strftime('%Y-%m-%d')}")
            if state.active_before: custom_filters.append(f"活跃早于 {state.active_before.strftime('%Y-%m-%d')}")
            
            if custom_filters:
                # 如果有自定义筛选条件，将它们添加到主筛选列表中
                filters.extend(custom_filters)

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

        state = self.search_state.model_dump()

        timeout_view = TimeoutView(self.cog, self.last_interaction, state)

        try:
            if not self.last_interaction:
                return

            edit_coro = self.last_interaction.edit_original_response(
                content="⏰ 搜索界面已超时，点击下方按钮可恢复之前的搜索状态。",
                view=timeout_view,
                embeds=[],
            )
            await self.cog.bot.api_scheduler.submit(
                coro_factory=lambda: edit_coro, priority=1
            )
        except (discord.errors.NotFound, discord.errors.HTTPException):
            pass
