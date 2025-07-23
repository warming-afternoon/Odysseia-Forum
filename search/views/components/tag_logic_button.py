import discord

class TagLogicButton(discord.ui.Button):
    def __init__(self, current_logic: str, update_callback):
        self.current_logic = current_logic
        label = "匹配: 同时" if current_logic == "and" else "匹配: 任一"
        style = discord.ButtonStyle.primary if current_logic == "and" else discord.ButtonStyle.secondary
        super().__init__(label=label, style=style, row=2)
        self.update_callback = update_callback

    async def callback(self, interaction: discord.Interaction):
        self.view.tag_logic = "or" if self.view.tag_logic == "and" else "and"
        await self.update_callback(interaction)