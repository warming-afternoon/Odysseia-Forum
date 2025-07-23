import discord
from discord import app_commands
from discord.ext import commands
import asyncio

from sqlalchemy.orm import sessionmaker
from tag_system.cog import TagSystem
from .views import IndexerDashboard

class Indexer(commands.Cog):
    """构建索引相关命令"""

    def __init__(self, bot: commands.Bot, session_factory: sessionmaker):
        self.bot = bot
        self.session_factory = session_factory
        self.queue = asyncio.Queue()
        self.progress = {
            "discovered": 0,
            "processed": 0,
            "total": 0,
            "finished": False
        }

    def get_progress(self):
        return self.progress

    @app_commands.command(name="构建索引", description="对当前论坛频道的所有帖子进行索引")
    async def build_index(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.Thread):
            await self.bot.api_scheduler.submit(
                coro=interaction.response.send_message("请在论坛频道的帖子内使用此命令。", ephemeral=True),
                priority=1
            )
            return
        
        channel = interaction.channel.parent
        if not isinstance(channel, discord.ForumChannel):
            await self.bot.api_scheduler.submit(
                coro=interaction.response.send_message("此命令仅适用于论坛频道。", ephemeral=True),
                priority=1
            )
            return

        dashboard = IndexerDashboard(self, channel)
        await dashboard.start(interaction)

    async def run_indexer(self, dashboard: IndexerDashboard):
        """运行生产者和消费者任务"""
        self.progress = {"discovered": 0, "processed": 0, "total": 0, "finished": False}
        producer_task = self.bot.loop.create_task(self.producer(dashboard))
        consumer_task = self.bot.loop.create_task(self.consumer(dashboard))
        
        await asyncio.gather(producer_task, consumer_task)
        
        self.progress['finished'] = True
        await dashboard.update_embed()

        # 通知TagSystem刷新缓存
        tag_system_cog: TagSystem = self.bot.get_cog("TagSystem")
        if tag_system_cog:
            await tag_system_cog.refresh_indexed_channels_cache()

    async def producer(self, dashboard: IndexerDashboard):
        """生产者：发现帖子并放入队列"""
        channel = dashboard.channel
        
        # 活跃线程 (来自缓存，无API调用)
        for thread in channel.threads:
            await self.queue.put(thread)
            self.progress['discovered'] += 1
        await dashboard.update_embed()

        # 已归档线程 (手动分页，通过调度器获取)
        last_thread_timestamp = None
        while not dashboard.is_cancelled():
            # 将获取一个批次的操作作为一个协程，提交给调度器
            archived_threads_iterator = channel.archived_threads(limit=100, before=last_thread_timestamp)
            batch = await self.bot.api_scheduler.submit(
                coro=asyncio.gather(*[t async for t in archived_threads_iterator]),
                priority=10 # 这是低优先级后台任务
            )

            if not batch:
                break

            for thread in batch:
                await self.queue.put(thread)
                self.progress['discovered'] += 1
            
            last_thread_timestamp = batch[-1].created_at
            
            # 每处理完一个批次，更新一次UI
            await dashboard.update_embed()

        if not dashboard.is_cancelled():
            self.progress['total'] = self.progress['discovered']
            await dashboard.update_embed()

        await self.queue.put(None) # Sentinel to signal producer is done

    async def consumer(self, dashboard: IndexerDashboard):
        """消费者：从队列中取出帖子并处理"""
        tag_system_cog: TagSystem = self.bot.get_cog("TagSystem")

        while True:
            await dashboard.wait_if_paused()
            if dashboard.is_cancelled():
                break

            thread = await self.queue.get()
            if thread is None: # Sentinel reached
                self.queue.task_done()
                break
            
            if tag_system_cog:
                await tag_system_cog.sync_thread(thread)

            self.progress['processed'] += 1
            if self.progress['processed'] % 5 == 0: # Avoid updating too frequently
                await dashboard.update_embed()
            
            self.queue.task_done()
