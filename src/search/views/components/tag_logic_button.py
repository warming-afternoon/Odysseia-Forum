import discord


class TagLogicButton(discord.ui.Button):
    def __init__(self, tag_logic: str, update_callback, row: int = 0):
        label = "匹配: 同时" if tag_logic == "and" else "匹配: 任一"
        style = discord.ButtonStyle.primary
        super().__init__(label=label, style=style, row=row)
        self.update_callback = update_callback

    async def callback(self, interaction: discord.Interaction):
        await self.update_callback(interaction)
