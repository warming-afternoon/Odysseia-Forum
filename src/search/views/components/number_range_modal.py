import discord
from typing import TYPE_CHECKING, cast
from src.shared.default_preferences import DefaultPreferences

if TYPE_CHECKING:
    from ...dto.search_state import SearchStateDTO

class NumberRangeModal(discord.ui.Modal, title="设置数值范围"):
    def __init__(self, current_state: "SearchStateDTO", callback):
        super().__init__(timeout=300)
        self.callback = callback
        self.add_item(
            discord.ui.TextInput(
                label="反应数范围, 例如, [10, 100)",
                default=current_state.reaction_count_range,
                required=False,
            )
        )
        self.add_item(
            discord.ui.TextInput(
                label="回复数范围, 例如, (5, 50]",
                default=current_state.reply_count_range,
                required=False,
            )
        )

    async def on_submit(self, interaction: discord.Interaction):
        reaction_range_input = cast(discord.ui.TextInput, self.children[0])
        reply_range_input = cast(discord.ui.TextInput, self.children[1])

        values = {
            "reaction_count_range": reaction_range_input.value or DefaultPreferences.DEFAULT_NUMERIC_RANGE.value,
            "reply_count_range": reply_range_input.value or DefaultPreferences.DEFAULT_NUMERIC_RANGE.value,
        }
        
        # 不在这里解析，直接将原始字符串传回
        await self.callback(interaction, values)