import discord

class KeywordModal(discord.ui.Modal, title="设置关键词过滤"):
    def __init__(self, parent_view, update_callback):
        super().__init__()
        self.parent_view = parent_view
        self.update_callback = update_callback
        
        self.include_input = discord.ui.TextInput(
            label="包含关键词（逗号或斜杠分隔）",
            placeholder="在标题或首楼中必须包含的关键词",
            required=False,
            default=self.parent_view.keywords
        )
        self.add_item(self.include_input)

    async def on_submit(self, interaction: discord.Interaction):
        self.parent_view.keywords = self.include_input.value
        await self.update_callback(interaction)

class KeywordButton(discord.ui.Button):
    def __init__(self, update_callback):
        super().__init__(label="📝 关键词", style=discord.ButtonStyle.secondary, row=2)
        self.update_callback = update_callback

    async def callback(self, interaction: discord.Interaction):
        await self.view.cog.bot.api_scheduler.submit(
            coro=interaction.response.send_modal(KeywordModal(self.view, self.update_callback)),
            priority=1
        )