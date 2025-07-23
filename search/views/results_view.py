import discord
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..cog import Search
    from ..models.qo.thread_search import ThreadSearchQO

class NewSearchResultsView(discord.ui.View):
    def __init__(self, cog: "Search", interaction: discord.Interaction, search_qo: "ThreadSearchQO", total: int, page: int, per_page: int):
        super().__init__(timeout=900)
        self.cog = cog
        self.interaction = interaction
        self.search_qo = search_qo
        self.total = total
        self.page = page
        self.per_page = per_page
        self.max_page = max(1, math.ceil(total / per_page))

        self.update_buttons()

    def update_buttons(self):
        self.clear_items()
        
        first_page = discord.ui.Button(label="⏮️", style=discord.ButtonStyle.secondary, disabled=(self.page == 1))
        first_page.callback = self.go_to_first_page
        self.add_item(first_page)

        prev_page = discord.ui.Button(label="◀️", style=discord.ButtonStyle.secondary, disabled=(self.page == 1))
        prev_page.callback = self.go_to_previous_page
        self.add_item(prev_page)

        current_page_button = discord.ui.Button(label=f"{self.page}/{self.max_page}", style=discord.ButtonStyle.primary, disabled=True)
        self.add_item(current_page_button)

        next_page = discord.ui.Button(label="▶️", style=discord.ButtonStyle.secondary, disabled=(self.page == self.max_page))
        next_page.callback = self.go_to_next_page
        self.add_item(next_page)

        last_page = discord.ui.Button(label="⏭️", style=discord.ButtonStyle.secondary, disabled=(self.page == self.max_page))
        last_page.callback = self.go_to_last_page
        self.add_item(last_page)

    async def go_to_page(self, interaction: discord.Interaction, page: int):
        await self.cog.bot.api_scheduler.submit(
            coro=interaction.response.defer(),
            priority=1
        )
        self.page = page
        
        results = await self.cog._search_and_display(interaction, self.search_qo, self.page)
        
        if results['has_results']:
            self.update_buttons()
            content = f"搜索结果：找到 {self.total} 个帖子 (第{self.page}/{self.max_page}页)"
            await self.cog.bot.api_scheduler.submit(
                coro=self.interaction.edit_original_response(content=content, embeds=results['embeds'], view=self),
                priority=1
            )
        else:
            await self.cog.bot.api_scheduler.submit(
                coro=self.interaction.edit_original_response(content="没有更多结果了。", embeds=[], view=None),
                priority=1
            )

    async def go_to_first_page(self, interaction: discord.Interaction):
        await self.go_to_page(interaction, 1)

    async def go_to_previous_page(self, interaction: discord.Interaction):
        await self.go_to_page(interaction, self.page - 1)

    async def go_to_next_page(self, interaction: discord.Interaction):
        await self.go_to_page(interaction, self.page + 1)

    async def go_to_last_page(self, interaction: discord.Interaction):
        await self.go_to_page(interaction, self.max_page)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        
        # 确保超时后禁用按钮的交互也能被调度
        try:
            await self.cog.bot.api_scheduler.submit(
                coro=self.interaction.edit_original_response(view=self),
                priority=1
            )
        except discord.errors.NotFound:
            # 如果原始消息被删除，忽略错误
            pass