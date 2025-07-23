import discord

class SortOrderButton(discord.ui.Button):
    def __init__(self, sort_order: str, update_callback):
        label = "📉 降序" if sort_order == "desc" else "📈 升序"
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=2)
        self.update_callback = update_callback

    async def callback(self, interaction: discord.Interaction):
        self.view.sort_order = "asc" if self.view.sort_order == "desc" else "desc"
        await self.update_callback(interaction)