import discord
import datetime
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ...dto.search_state import SearchStateDTO

class TimeRangeModal(discord.ui.Modal, title="设置时间范围 (YYYY-MM-DD)"):
    def __init__(self, current_state: "SearchStateDTO", callback):
        super().__init__(timeout=300)
        self.callback = callback
        self.add_item(discord.ui.TextInput(label="发帖时间 (晚于)", default=current_state.created_after.strftime('%Y-%m-%d') if current_state.created_after else "", required=False))
        self.add_item(discord.ui.TextInput(label="发帖时间 (早于)", default=current_state.created_before.strftime('%Y-%m-%d') if current_state.created_before else "", required=False))
        self.add_item(discord.ui.TextInput(label="活跃时间 (晚于)", default=current_state.active_after.strftime('%Y-%m-%d') if current_state.active_after else "", required=False))
        self.add_item(discord.ui.TextInput(label="活跃时间 (早于)", default=current_state.active_before.strftime('%Y-%m-%d') if current_state.active_before else "", required=False))

    async def on_submit(self, interaction: discord.Interaction):
        values = {}
        try:
            created_after_input = cast(discord.ui.TextInput, self.children[0])
            created_before_input = cast(discord.ui.TextInput, self.children[1])
            active_after_input = cast(discord.ui.TextInput, self.children[2])
            active_before_input = cast(discord.ui.TextInput, self.children[3])

            values["created_after"] = datetime.datetime.strptime(created_after_input.value, '%Y-%m-%d') if created_after_input.value else None
            values["created_before"] = datetime.datetime.strptime(created_before_input.value, '%Y-%m-%d') if created_before_input.value else None
            values["active_after"] = datetime.datetime.strptime(active_after_input.value, '%Y-%m-%d') if active_after_input.value else None
            values["active_before"] = datetime.datetime.strptime(active_before_input.value, '%Y-%m-%d') if active_before_input.value else None
            
            await self.callback(interaction, values)
        except (ValueError, TypeError):
            await interaction.response.send_message("输入的时间格式不正确，请确保使用 `YYYY-MM-DD` 格式。", ephemeral=True, delete_after=10)