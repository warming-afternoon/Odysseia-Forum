import discord
from typing import TYPE_CHECKING, cast
from src.shared.default_preferences import DefaultPreferences
from src.shared.range_parser import parse_range_string, InvalidRangeFormat

if TYPE_CHECKING:
    from ...dto.search_state import SearchStateDTO

class NumberRangeModal(discord.ui.Modal, title="设置数值范围"):
    def __init__(self, current_state: "SearchStateDTO", callback):
        super().__init__(timeout=840)
        self.callback = callback
        
        # 准备一个没有 label 的 TextInput 组件
        default_range = DefaultPreferences.DEFAULT_NUMERIC_RANGE.value
        reaction_default = default_range if current_state.reaction_count_range == default_range else current_state.reaction_count_range
        
        reaction_input = discord.ui.TextInput(
            default=reaction_default,
            required=False,
        )

        # 使用 Label 组件进行说明
        self.add_item(discord.ui.Label(
            text="反应数范围 (使用数学区间表示法)",
            description=(
                f"什么是数学区间表示法 :  \n\n[ ] 包含边界, ( ) 不含边界。  \n\n"
                f"例如: [10, 100) 代表 ≥10 且 <100。"
            ),
            component=reaction_input
        ))
        
        # --- 结束修改 ---
        
        reply_default = default_range if current_state.reply_count_range == default_range else current_state.reply_count_range
        
        self.add_item(
            discord.ui.TextInput(
                label="回复数范围 (使用数学区间表示法)", # 这个是合法的，因为它没在 Label 里
                default=reply_default,
                required=False,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        reaction_range_input = cast(discord.ui.TextInput, self.children[0].component)
        reply_range_input = cast(discord.ui.TextInput, self.children[1])

        reaction_value = reaction_range_input.value or DefaultPreferences.DEFAULT_NUMERIC_RANGE.value
        reply_value = reply_range_input.value or DefaultPreferences.DEFAULT_NUMERIC_RANGE.value

        try:
            if reaction_range_input.value:
                parse_range_string(reaction_value)
            
            if reply_range_input.value:
                parse_range_string(reply_value)

        except InvalidRangeFormat as e:
            await interaction.response.send_message(f"❌ 输入有误：{e}", ephemeral=True, delete_after=120)
            return

        values = {
            "reaction_count_range": reaction_value,
            "reply_count_range": reply_value,
        }
        await self.callback(interaction, values)