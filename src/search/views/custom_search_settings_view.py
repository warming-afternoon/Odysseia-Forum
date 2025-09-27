import discord
from typing import TYPE_CHECKING, Optional, cast

from src.shared.safe_defer import safe_defer
from .components.sort_method_select import SortMethodSelect
from .components.number_range_modal import NumberRangeModal
from .components.time_range_modal import TimeRangeModal

if TYPE_CHECKING:
    from .generic_search_view import GenericSearchView


class CustomSearchSettingsView(discord.ui.View):
    def __init__(self, parent_view: "GenericSearchView"):
        super().__init__(timeout=600)
        self.parent = parent_view
        self.state = parent_view.search_state
        self.original_interaction = parent_view.last_interaction
        self.update_components()

    def update_components(self):
        self.clear_items()
        self.add_item(discord.ui.Button(label="设置数值范围", style=discord.ButtonStyle.secondary, custom_id="set_num_range", row=0))
        self.add_item(discord.ui.Button(label="设置时间范围", style=discord.ButtonStyle.secondary, custom_id="set_time_range", row=0))
        
        # 移除自定义选项本身，只保留基础排序
        base_sort_select = SortMethodSelect(self.state.custom_base_sort, self.on_base_sort_change, row=1)
        base_sort_select.options = [opt for opt in base_sort_select.options if opt.value != "custom"]
        self.add_item(base_sort_select)
        # 绑定回调
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.callback = self.button_callback
    
    def build_embed(self):
        embed = discord.Embed(title="🛠️ 自定义搜索设置", description="配置精细的筛选条件，然后选择一种基础排序算法。", color=discord.Color.blue())
        
        # 数值范围
        num_range_value = (
            f"反应数: `{self.state.reaction_count_range}`\n"
            f"回复数: `{self.state.reply_count_range}`"
        )
        embed.add_field(name="🔢 数值范围", value=num_range_value, inline=False)

        # 时间范围
        time_range_parts = []
        if self.state.created_after: time_range_parts.append(f"发帖晚于: `{self.state.created_after.strftime('%Y-%m-%d')}`")
        if self.state.created_before: time_range_parts.append(f"发帖早于: `{self.state.created_before.strftime('%Y-%m-%d')}`")
        if self.state.active_after: time_range_parts.append(f"活跃晚于: `{self.state.active_after.strftime('%Y-%m-%d')}`")
        if self.state.active_before: time_range_parts.append(f"活跃早于: `{self.state.active_before.strftime('%Y-%m-%d')}`")
        time_range_value = ", ".join(time_range_parts) if time_range_parts else "未设置"
        embed.add_field(name="📅 时间范围", value=time_range_value, inline=False)

        return embed

    async def start(self) -> Optional[discord.WebhookMessage]:
        """发送配置视图并返回消息对象"""
        return await self.original_interaction.followup.send(embed=self.build_embed(), view=self, ephemeral=True, wait=True)

    async def button_callback(self, interaction: discord.Interaction):
        if not interaction.data:
            await safe_defer(interaction)
            return

        custom_id = interaction.data.get("custom_id")
        if custom_id == "set_num_range":
            await interaction.response.send_modal(NumberRangeModal(self.state, self.handle_num_modal_submit))
        elif custom_id == "set_time_range":
            await interaction.response.send_modal(TimeRangeModal(self.state, self.handle_time_modal_submit))

    async def _update_and_research(self, interaction: discord.Interaction):
        """统一的更新和重新搜索入口"""
        # defer 配置视图自己的交互，以释放它
        await interaction.response.defer()
        
        # 调用父视图的方法，让父视图用它自己的 last_interaction 去更新主消息
        await self.parent.trigger_search_from_custom_settings(self.state)
        
        # 父视图更新完成后，再用 webhook 安全地编辑本视图的消息
        await interaction.edit_original_response(embed=self.build_embed(), view=self)

    async def on_base_sort_change(self, interaction: discord.Interaction, new_method: str):
        self.state.custom_base_sort = new_method
        await self._update_and_research(interaction)

    async def handle_num_modal_submit(self, interaction: discord.Interaction, values: dict):
        self.state.reaction_count_range = values.get("reaction_count_range", "[0, 10000000)")
        self.state.reply_count_range = values.get("reply_count_range", "[0, 10000000)")
        await self._update_and_research(interaction)
    
    async def handle_time_modal_submit(self, interaction: discord.Interaction, values: dict):
        self.state.created_after = values.get("created_after")
        self.state.created_before = values.get("created_before")
        self.state.active_after = values.get("active_after")
        self.state.active_before = values.get("active_before")
        await self._update_and_research(interaction)