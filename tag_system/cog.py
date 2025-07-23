import discord
from discord import app_commands
from discord.ext import commands
import datetime

from .repository import TagSystemRepository
from shared.database import get_session 

class TagSystem(commands.Cog):
    """处理标签同步与评价"""

    def __init__(self, bot: commands.Bot, repository: TagSystemRepository):
        self.bot = bot
        self.repository = repository
        self.indexed_channel_ids = set()  # 缓存已索引的频道ID

    async def cog_load(self):
        """Cog加载时初始化缓存"""
        await self.refresh_indexed_channels_cache()

    async def refresh_indexed_channels_cache(self):
        """刷新已索引频道的缓存"""
        self.indexed_channel_ids = set(await self.repository.get_indexed_channel_ids())
        print(f"已缓存的索引频道: {self.indexed_channel_ids}")

    def is_channel_indexed(self, channel_id: int) -> bool:
        """检查频道是否已索引"""
        return channel_id in self.indexed_channel_ids

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if self.is_channel_indexed(thread.parent_id):
            # 事件触发的同步是高优先级
            await self.sync_thread(thread, priority=1)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if self.is_channel_indexed(after.parent_id) and before.applied_tags != after.applied_tags:
            # 事件触发的同步是高优先级
            await self.sync_thread(after, priority=1)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        if self.is_channel_indexed(thread.parent_id):
            await self.repository.delete_thread_index(thread.id)
            await self.refresh_indexed_channels_cache()

    # 其他监听器（on_message, on_raw_message_edit, etc.）可以暂时保持不变，
    # 因为它们主要更新活跃时间等信息，这部分逻辑在 sync_thread 中已经覆盖。
    # 为了简化，我们将主要依赖 on_thread_create/update/delete。
    # 也可以在未来细化，只更新部分字段以提高性能。

    async def sync_thread(self, thread: discord.Thread, priority: int = 10):
        """
        同步一个帖子的数据到数据库，包括其标签。
        这是一个核心方法，由事件监听器和索引器调用。
        :param thread: 要同步的帖子对象。
        :param priority: 此操作的API调用优先级。
        """
        # 将频道添加到已索引缓存中
        self.indexed_channel_ids.add(thread.parent_id)
        
        tag_names = [t.name for t in thread.applied_tags or []]
        
        excerpt = ""
        thumbnail_url = ""
        reaction_count = 0
        try:
            # 使用调度器以指定优先级获取消息
            first_msg = await self.bot.api_scheduler.submit(
                coro=thread.fetch_message(thread.id),
                priority=priority
            )
            if first_msg:
                excerpt = first_msg.content
                if first_msg.attachments:
                    thumbnail_url = first_msg.attachments[0].url
                reaction_count = max([r.count for r in first_msg.reactions]) if first_msg.reactions else 0
        except discord.NotFound:
            print(f"无法获取帖子 {thread.id} 的首楼消息，可能已被删除。")
            await self.repository.delete_thread_index(thread.id)
            return
        except Exception as e:
            print(f"同步帖子 {thread.id} 时获取首楼消息失败: {e}")

        thread_data = {
            "thread_id": thread.id,
            "channel_id": thread.parent_id,
            "title": thread.name,
            "author_id": thread.owner_id or 0,
            "created_at": thread.created_at,
            "last_active_at": datetime.datetime.now(datetime.timezone.utc), # 简化处理，每次同步都更新活跃时间
            "reaction_count": reaction_count,
            "reply_count": thread.message_count,
            "first_message_excerpt": excerpt,
            "thumbnail_url": thumbnail_url,
        }
        
        await self.repository.add_or_update_thread_with_tags(thread_data, tag_names)
        print(f"已同步帖子: {thread.name} (ID: {thread.id})")

    # 标签评价相关的功能暂时移除，因为它们依赖于旧的 tag_votes 表，
    # 新的模型中没有这个表。如果需要此功能，需要重新设计。
    # @app_commands.command(name="标签评价", ...
    # @app_commands.command(name="查看帖子标签", ...