import discord
import asyncio

class IndexerDashboard(discord.ui.View):
    """索引器仪表板视图，用于控制和显示索引过程。"""

    def __init__(self, cog, channel: discord.ForumChannel):
        super().__init__(timeout=None)
        self.cog = cog
        self.channel = channel
        self.interaction: discord.Interaction = None
        self.message: discord.Message = None

        self._paused = asyncio.Event()
        self._paused.set()  # Initially not paused
        self._cancelled = asyncio.Event()

    async def start(self, interaction: discord.Interaction):
        """启动仪表板并发送初始消息。"""
        self.interaction = interaction
        embed = self.create_embed()
        await self.cog.bot.api_scheduler.submit(
            coro=interaction.response.send_message(embed=embed, view=self, ephemeral=True),
            priority=1
        )
        self.message = await self.cog.bot.api_scheduler.submit(
            coro=interaction.original_response(),
            priority=1
        )
        self.cog.bot.loop.create_task(self.cog.run_indexer(self))

    def create_embed(self, progress_stats=None):
        """创建或更新嵌入消息。"""
        progress_stats = progress_stats or self.cog.get_progress()
        
        title = f"正在索引频道: #{self.channel.name}"
        if self.is_paused():
            state = "⏸️ 已暂停"
        elif self.is_cancelled():
            state = "⏹️ 已取消"
        elif progress_stats['finished']:
            state = "✅ 已完成"
        else:
            state = "⚙️ 运行中"

        embed = discord.Embed(title=title, color=discord.Color.blue())
        embed.add_field(name="状态", value=state, inline=False)
        embed.add_field(name="已发现帖子", value=str(progress_stats['discovered']), inline=True)
        embed.add_field(name="已处理帖子", value=f"{progress_stats['processed']} / {progress_stats['total']}", inline=True)

        return embed

    async def update_embed(self):
        """更新嵌入消息。"""
        if self.message:
            embed = self.create_embed()
            # 使用中等优先级更新进度，以免阻塞高优任务
            await self.cog.bot.api_scheduler.submit(
                coro=self.message.edit(embed=embed, view=self),
                priority=5
            )

    @discord.ui.button(label="暂停", style=discord.ButtonStyle.secondary, custom_id="indexer_pause")
    async def pause_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self._paused.is_set():
            self._paused.clear()
            button.label = "继续"
            button.style = discord.ButtonStyle.success
        else:
            self._paused.set()
            button.label = "暂停"
            button.style = discord.ButtonStyle.secondary
        
        await self.update_embed()
        await self.cog.bot.api_scheduler.submit(
            coro=interaction.response.defer(),
            priority=1
        )

    @discord.ui.button(label="取消", style=discord.ButtonStyle.danger, custom_id="indexer_cancel")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self._cancelled.set()
        self.pause_button.disabled = True
        button.disabled = True
        await self.update_embed()
        await self.cog.bot.api_scheduler.submit(
            coro=interaction.response.defer(),
            priority=1
        )

    def is_paused(self) -> bool:
        return not self._paused.is_set()

    def is_cancelled(self) -> bool:
        return self._cancelled.is_set()

    async def wait_if_paused(self):
        await self._paused.wait()