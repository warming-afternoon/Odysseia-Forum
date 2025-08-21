import discord
from discord import app_commands
from discord.ext import commands
import datetime
from sqlalchemy.orm import sessionmaker
from typing import Coroutine

from shared.discord_utils import safe_defer
from .repository import TagSystemRepository
from .views.vote_view import TagVoteView

import logging

logger = logging.getLogger(__name__)


class TagSystem(commands.Cog):
    """处理标签同步与评价"""

    def __init__(self, bot: commands.Bot, session_factory: sessionmaker, config: dict):
        self.bot = bot
        self.session_factory = session_factory
        self.config = config
        self.indexed_channel_ids = set()  # 缓存已索引的频道ID

    async def cog_load(self):
        """Cog加载时初始化缓存"""
        await self.refresh_indexed_channels_cache()

    @commands.Cog.listener()
    async def on_index_updated(self):
        """监听由 Indexer 发出的索引更新事件。"""
        logger.info("接收到 'index_updated' 事件，正在刷新标签系统的频道缓存...")
        await self.refresh_indexed_channels_cache()

    async def refresh_indexed_channels_cache(self):
        """刷新已索引频道的缓存"""
        async with self.session_factory() as session:
            repo = TagSystemRepository(session)
            self.indexed_channel_ids = set(await repo.get_indexed_channel_ids())
        logger.info(f"已缓存的索引频道: {self.indexed_channel_ids}")

    def is_channel_indexed(self, channel_id: int) -> bool:
        """检查频道是否已索引"""
        return channel_id in self.indexed_channel_ids

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if self.is_channel_indexed(channel_id=thread.parent_id):
            # 事件触发的同步是高优先级
            await self.sync_thread(thread=thread, priority=1)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread, after: discord.Thread):
        if (
            self.is_channel_indexed(channel_id=after.parent_id)
            and before.applied_tags != after.applied_tags
        ):
            # 事件触发的同步是高优先级
            await self.sync_thread(thread=after, priority=1)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        if self.is_channel_indexed(thread.parent_id):
            async with self.session_factory() as session:
                repo = TagSystemRepository(session=session)
                await repo.delete_thread_index(thread_id=thread.id)
            await self.refresh_indexed_channels_cache()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or not isinstance(message.channel, discord.Thread):
            return

        thread = message.channel
        if self.is_channel_indexed(thread.parent_id):
            self.bot.loop.create_task(self._update_activity(thread, message.created_at))

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        if not payload.guild_id:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if isinstance(channel, discord.Thread) and self.is_channel_indexed(
                channel.parent_id
            ):
                # 如果是首楼消息被编辑，需要重新同步整个帖子
                if payload.message_id == channel.id:
                    # 因为这是 raw 事件，缓存的 channel 对象可能不是最新的
                    # 我们需要确保同步的是最完整的数据
                    await self.sync_thread(
                        thread=channel, priority=2, fetch_if_incomplete=True
                    )
                else:
                    # 普通消息编辑只更新活跃时间
                    self.bot.loop.create_task(
                        self._update_activity(
                            channel, datetime.datetime.now(datetime.timezone.utc)
                        )
                    )
        except Exception:
            logger.warning("处理消息编辑事件失败", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(self, payload: discord.RawMessageDeleteEvent):
        if not payload.guild_id:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if isinstance(channel, discord.Thread) and self.is_channel_indexed(
                channel.parent_id
            ):
                # 如果首楼被删除，删除整个索引
                if payload.message_id == channel.id:
                    async with self.session_factory() as session:
                        repo = TagSystemRepository(session=session)
                        await repo.delete_thread_index(thread_id=channel.id)
                    await self.refresh_indexed_channels_cache()
                else:
                    # 普通消息删除，更新回复数和活跃时间
                    self.bot.loop.create_task(self._update_activity(channel))
        except Exception:
            logger.warning("处理消息删除事件失败", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if isinstance(channel, discord.Thread) and self.is_channel_indexed(
                channel.parent_id
            ):
                # 只有对首楼消息的反应才更新统计
                if payload.message_id == channel.id:
                    await self.bot.api_scheduler.submit(
                        coro=self._update_reaction_count(channel), priority=5
                    )
        except Exception:
            logger.warning("处理反应添加事件失败", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent):
        if not payload.guild_id:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if isinstance(channel, discord.Thread) and self.is_channel_indexed(
                channel.parent_id
            ):
                # 只有对首楼消息的反应才更新统计
                if payload.message_id == channel.id:
                    await self.bot.api_scheduler.submit(
                        coro=self._update_reaction_count(channel), priority=5
                    )
        except Exception:
            logger.warning("处理反应移除事件失败", exc_info=True)

    async def _update_activity(
        self, thread: discord.Thread, last_active_time: datetime = None
    ):
        """(协程) 更新帖子的活跃度和回复数"""
        if last_active_time is None:
            # 如果没有提供时间，就用当前时间
            last_active_time = datetime.datetime.now(datetime.timezone.utc)

        # message_count 通常是准确的，除非有大量删除
        reply_count = thread.message_count

        async with self.session_factory() as session:
            repo = TagSystemRepository(session)
            await repo.update_thread_activity(thread.id, last_active_time, reply_count)

    async def _update_reaction_count(self, thread: discord.Thread):
        """(协程) 更新帖子的反应数"""
        try:
            # 优先从缓存获取，失败则API调用
            first_msg = thread.get_partial_message(thread.id)
            first_msg = await first_msg.fetch()

            reaction_count = (
                max([r.count for r in first_msg.reactions])
                if first_msg.reactions
                else 0
            )

            async with self.session_factory() as session:
                repo = TagSystemRepository(session)
                await repo.update_thread_reaction_count(thread.id, reaction_count)
        except Exception:
            logger.warning(f"更新反应数失败 (帖子ID: {thread.id})", exc_info=True)

    async def pre_sync_forum_tags(self, channel: discord.ForumChannel):
        """
        预同步一个论坛频道的所有可用标签，确保它们都存在于数据库中。
        """
        logger.debug(
            f"开始为频道 '{channel.name}' (ID: {channel.id}) 预同步所有可用标签..."
        )
        if not channel.available_tags:
            logger.debug(
                f"频道 '{channel.name}' (ID: {channel.id}) 没有任何可用标签，跳过同步。"
            )
            return

        tags_data = {tag.id: tag.name for tag in channel.available_tags}

        try:
            async with self.session_factory() as session:
                repo = TagSystemRepository(session=session)
                await repo.get_or_create_tags(tags_data)
            logger.debug(
                f"为频道 '{channel.name}' (ID: {channel.id}) 预同步了 {len(tags_data)} 个标签。"
            )
        except Exception as e:
            logger.error(
                f"为频道 '{channel.name}' (ID: {channel.id}) 预同步标签时出错: {e}",
                exc_info=True,
            )
            # 即使这里失败，我们也不应该中断整个索引过程，
            # 因为后续的 consumer 仍然有机会（虽然有风险）去创建标签。
            # 抛出异常让调用者决定如何处理。
            raise

    @staticmethod
    async def _fetch_message_wrapper(fetch_coro: Coroutine) -> discord.Message | None:
        """
        包装一个获取消息的协程
        如果协程成功，返回消息对象；如果抛出 NotFound，返回 None。
        """
        try:
            return await fetch_coro
        except discord.NotFound:
            return None

    async def sync_thread(
        self,
        thread: discord.Thread,
        priority: int = 10,
        *,
        fetch_if_incomplete: bool = False,
    ):
        """
        同步一个帖子的数据到数据库，包括其标签。
        该方法由事件监听器和索引器调用。
        :param thread: 要同步的帖子对象。
        :param priority: 此操作的API调用优先级。
        :param fetch_if_incomplete: 如果为True，则强制从API获取最新的帖子对象，用于处理可能不完整的对象。
        """
        if fetch_if_incomplete:
            try:
                thread = await self.bot.api_scheduler.submit(
                    coro=self.bot.fetch_channel(thread.id), priority=priority
                )
            except discord.NotFound:
                logger.warning(f"sync_thread: 无法找到帖子 {thread.id}，可能已被删除。")

                async with self.session_factory() as session:
                    repo = TagSystemRepository(session=session)
                    await repo.delete_thread_index(thread_id=thread.id)
                return

        # 将频道添加到已索引缓存中
        self.indexed_channel_ids.add(thread.parent_id)

        tags_data = {t.id: t.name for t in thread.applied_tags or []}

        excerpt = ""
        thumbnail_url = ""
        reaction_count = 0

        # 创建原始的获取消息的协程，并用包装器包裹它
        fetch_coro = thread.fetch_message(thread.id)
        first_msg = await self.bot.api_scheduler.submit(
            coro=self._fetch_message_wrapper(fetch_coro), priority=priority
        )

        # 如果返回 None，说明帖子已被删除，记录日志并从数据库删除
        if first_msg is None:
            logger.debug(
                f"无法获取帖子 {thread.id} 的首楼消息，其可能已被删除\n已将其从索引中删除"
            )
            async with self.session_factory() as session:
                repo = TagSystemRepository(session=session)
                await repo.delete_thread_index(thread_id=thread.id)
            return

        # 消息获取成功，但解析内容时可能出错
        try:
            excerpt = first_msg.content
            if first_msg.attachments:
                thumbnail_url = first_msg.attachments[0].url
            reaction_count = (
                max([r.count for r in first_msg.reactions])
                if first_msg.reactions
                else 0
            )
        except Exception:
            logger.error(f"同步帖子 {thread.id} 时解析首楼消息内容失败", exc_info=True)

        thread_data = {
            "thread_id": thread.id,
            "channel_id": thread.parent_id,
            "title": thread.name,
            "author_id": thread.owner_id or 0,
            "created_at": thread.created_at,
            "last_active_at": thread.last_message.created_at
            if thread.last_message
            else thread.created_at,
            "reaction_count": reaction_count,
            "reply_count": thread.message_count,
            "first_message_excerpt": excerpt,
            "thumbnail_url": thumbnail_url,
        }

        async with self.session_factory() as session:
            repo = TagSystemRepository(session=session)
            await repo.add_or_update_thread_with_tags(
                thread_data=thread_data, tags_data=tags_data
            )
        # logger.info(f"已同步帖子: {thread.name} (ID: {thread.id})")

    @app_commands.command(
        name="标签评价", description="对当前帖子的标签进行评价（赞或踩）"
    )
    async def tag_rate(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        try:
            if not isinstance(interaction.channel, discord.Thread):
                await self.bot.api_scheduler.submit(
                    coro=interaction.followup.send(
                        "此命令只能在帖子中使用。", ephemeral=True
                    ),
                    priority=1,
                )
                return

            if not interaction.channel.applied_tags:
                await self.bot.api_scheduler.submit(
                    coro=interaction.followup.send(
                        "该帖子没有应用任何标签。", ephemeral=True
                    ),
                    priority=1,
                )
                return

            tag_map = {tag.id: tag.name for tag in interaction.channel.applied_tags}

            view = TagVoteView(
                thread_id=interaction.channel.id,
                thread_name=interaction.channel.name,
                tag_map=tag_map,
                session_factory=self.session_factory,
                api_scheduler=self.bot.api_scheduler,
            )
            # 获取初始统计数据
            async with self.session_factory() as session:
                repo = TagSystemRepository(session)
                initial_stats = await repo.get_tag_vote_stats(
                    interaction.channel.id, tag_map
                )

            # 使用初始统计数据创建嵌入
            embed = view.create_embed(initial_stats)

            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(embed=embed, view=view, ephemeral=True),
                priority=1,
            )
        except Exception as e:
            await self.bot.api_scheduler.submit(
                coro=interaction.followup.send(f"❌ 命令执行失败: {e}", ephemeral=True),
                priority=1,
            )
