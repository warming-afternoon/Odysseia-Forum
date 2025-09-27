import discord

from src.search.constants import SortMethod


class SortMethodSelect(discord.ui.Select):
    def __init__(self, current_sort: str, update_callback, row: int = 0):
        options = []
        for method in SortMethod:
            info = method.value
            options.append(
                discord.SelectOption(
                    label=info.label,
                    value=info.value,
                    description=info.description,
                    default=(current_sort == info.value),
                )
            )

        super().__init__(placeholder="选择排序方式...", options=options, row=row)
        self.update_callback = update_callback

    async def callback(self, interaction: discord.Interaction):
        await self.update_callback(interaction, self.values[0])
