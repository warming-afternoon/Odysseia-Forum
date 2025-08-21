import discord


class KeywordModal(discord.ui.Modal, title="设置关键词过滤"):
    def __init__(
        self, initial_keywords: str, initial_exclude_keywords: str, submit_callback
    ):
        super().__init__()
        self.submit_callback = submit_callback

        self.include_input = discord.ui.TextInput(
            label="包含关键词（逗号或斜杠分隔）",
            placeholder="在标题或首楼中必须包含的关键词",
            required=False,
            default=initial_keywords,
        )
        self.add_item(self.include_input)

        self.exclude_input = discord.ui.TextInput(
            label="排除关键词（逗号分隔）",
            placeholder="在标题或首楼中不能包含的关键词",
            required=False,
            default=initial_exclude_keywords,
        )
        self.add_item(self.exclude_input)

    async def on_submit(self, interaction: discord.Interaction):
        await self.submit_callback(
            interaction, self.include_input.value, self.exclude_input.value
        )


class KeywordButton(discord.ui.Button):
    def __init__(self, press_callback, row: int = 2):
        super().__init__(
            label="📝 关键词", style=discord.ButtonStyle.secondary, row=row
        )
        self.press_callback = press_callback

    async def callback(self, interaction: discord.Interaction):
        await self.press_callback(interaction)
