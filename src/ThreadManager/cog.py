import asyncio
import datetime
from typing import TYPE_CHECKING
import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.follow_repository import ThreadFollowRepository
from core.thread_repository import ThreadRepository
from shared.safe_defer import safe_defer
from shared.enum.constant_enum import ConstantEnum
from ThreadManager.batch_update_service import BatchUpdateService
from ThreadManager.thread_logic import ThreadLogic
from ThreadManager.views.visibility_view import ThreadVisibilityView
from ThreadManager.views.vote_view import TagVoteView
from core.redis_trend_service import RedisTrendService

import logging
logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from bot_main import MyBot

class ThreadManager(commands.Cog):
    """处理帖子同步、状态检测与评价"""

    def __init__(self, bot: "MyBot", session_factory: async_sessionmaker,
                 config: dict):
        self.bot = bot
        self.session_factory = session_factory
        self.config = config
        self.cache_service = bot.cache_service
        self.sync_service = bot.sync_service
        
        # 初始化后台更新服务
        update_interval = self.config.get("performance", {}).get(
            "batch_update_interval", 30
        )
        self.batch_update_service = BatchUpdateService(
            session_factory, sync_service=self.sync_service,
            interval=update_interval
        )
        
        # 实例化业务逻辑处理器
        self.logic = ThreadLogic(bot, session_factory, config,
                                 self.sync_service)
        logger.info("ThreadManager 模块已加载")

    async def cog_load(self):
        """当 Cog 加载时，启动后台任务，并注册持久化视图。"""
        self.batch_update_service.start()
        # 注册可见性切换的持久化视图
        self.bot.add_view(ThreadVisibilityView(self.bot,
                                               self.session_factory))

    async def cog_unload(self):
        """当 Cog 卸载时，确保所有数据都被写入。"""
        await self.batch_update_service.stop()

    def is_channel_indexed(self, channel_id: int) -> bool:
        """检查频道是否已索引"""
        return self.cache_service.is_channel_indexed(channel_id)

    # ---------------------------------------------------------
    # 事件监听器
    # ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread):
        if self.is_channel_indexed(channel_id=thread.parent_id):
            # 延时 5s 再进行同步。减小请求失败概率
            await asyncio.sleep(5)
            modified = await self.logic.apply_mutex_tag_rules(thread)
            if not modified:
                await self.sync_service.sync_thread(thread=thread)

            # 自动关注
            if thread.owner_id and (
                not thread.owner.bot if thread.owner else True
            ):
                async with self.session_factory() as session:
                    follow_service = ThreadFollowRepository(session)
                    await follow_service.add_follow(
                        user_id=thread.owner_id,
                        thread_id=thread.id,
                        auto_view=False  # 贴主发布时不标记为已查看
                    )

    @commands.Cog.listener()
    async def on_thread_member_join(self, member: discord.ThreadMember):
        try:
            thread = member.thread
            if not thread or not self.is_channel_indexed(thread.parent_id):
                return

            # 优先从本地服务器缓存获取，如果没有则从全局用户缓存获取
            user = thread.guild.get_member(member.id) or self.bot.get_user(member.id)
            
            # 如果缓存里能找到这个用户，并且它是机器人，则跳过
            if user and user.bot:
                return

            async with self.session_factory() as session:
                follow_service = ThreadFollowRepository(session)
                # 用户主动加入时，标记为已查看
                await follow_service.add_follow(
                    user_id=member.id, thread_id=thread.id, auto_view=True
                )
        except Exception as e:
            logger.error(f"用户加入帖子自动关注失败: {e}", exc_info=True)

    @commands.Cog.listener()
    async def on_thread_update(self, before: discord.Thread,
                               after: discord.Thread):
        if self.is_channel_indexed(channel_id=after.parent_id) and (
            before.applied_tags != after.applied_tags
            or before.name != after.name
        ):
            modified = await self.logic.apply_mutex_tag_rules(after)
            if not modified:
                await self.sync_service.sync_thread(thread=after)

    @commands.Cog.listener()
    async def on_thread_delete(self, thread: discord.Thread):
        """当整个 Thread 被从 Discord 删除时触发"""
        if self.is_channel_indexed(thread.parent_id):
            await self.logic.delete_thread_permanently(thread.id)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if (
            not message.guild
            or not isinstance(message.channel, discord.Thread)
            or message.author.bot
        ):
            return

        thread = message.channel
        if thread.id == message.id:
            return

        if self.is_channel_indexed(thread.parent_id):
            await self.batch_update_service.add_update(
                thread.id, message.created_at
            )

    @commands.Cog.listener()
    async def on_raw_message_edit(
        self,
        payload: discord.RawMessageUpdateEvent
        ):
        if not payload.guild_id:
            return

        try:
            channel = self.bot.get_channel(payload.channel_id)
            if (isinstance(channel, discord.Thread)
                    and self.is_channel_indexed(channel.parent_id)):
                if payload.message_id == channel.id:
                    await self.sync_service.sync_thread(
                        thread=channel, fetch_if_incomplete=True
                    )
                else:
                    async with self.session_factory() as session:
                        repo = ThreadRepository(session)
                        await repo.update_thread_last_active_at(
                            channel.id,
                            datetime.datetime.now(datetime.timezone.utc)
                        )
        except Exception:
            logger.warning("处理消息编辑事件失败", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_message_delete(
        self,
        payload: discord.RawMessageDeleteEvent
        ):
        if not payload.guild_id:
            return
        try:
            channel = self.bot.get_channel(payload.channel_id)
            if (isinstance(channel, discord.Thread)
                    and self.is_channel_indexed(channel.parent_id)):
                # 如果首楼被删除，隐藏帖子，并在楼内发送公示视图
                if payload.message_id == channel.id:
                    await self.logic.handle_first_message_deletion(channel)
                else:
                    await self.batch_update_service.add_deletion(channel.id)
        except Exception:
            logger.warning("处理消息删除事件失败", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_add(
        self,
        payload: discord.RawReactionActionEvent
        ):
        if not payload.guild_id:
            return
        try:
            channel = self.bot.get_channel(payload.channel_id)
            # 只有对首楼消息的反应才更新统计，且需要该帖所在频道已索引
            if (
                isinstance(channel, discord.Thread)
                and self.is_channel_indexed(channel.parent_id)
                and payload.message_id == channel.id
            ):
                # 检查帖子创建时间
                if channel.created_at:
                    threshold = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=ConstantEnum.STATISTICS_THRESHOLD_DAYS.value)
                    if channel.created_at >= threshold:
                        # 只有 60 天内创建的帖子才记录点赞飙升
                        await RedisTrendService().record_increment(
                            "reaction", channel.id, 1
                        )
                
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: self.logic.update_reaction_count_and_sync(channel),  # noqa: E501
                    priority=5
                )
        except Exception:
            logger.warning("处理反应添加事件失败", exc_info=True)

    @commands.Cog.listener()
    async def on_raw_reaction_remove(
        self,
        payload: discord.RawReactionActionEvent
        ):
        if not payload.guild_id:
            return
        try:
            channel = self.bot.get_channel(payload.channel_id)
            if (
                isinstance(channel, discord.Thread)
                and self.is_channel_indexed(channel.parent_id)
                and payload.message_id == channel.id
            ):
                await self.bot.api_scheduler.submit(
                    coro_factory=lambda: self.logic.update_reaction_count_and_sync(channel),  # noqa: E501
                    priority=5
                )
        except Exception:
            logger.warning("处理反应移除事件失败", exc_info=True)

    # ---------------------------------------------------------
    # 指令区
    # ---------------------------------------------------------
    # @app_commands.command(name="发布更新",
    #                       description="发布帖子更新（仅贴主可用）")
    # @app_commands.describe(消息链接="更新消息的Discord链接")
    async def publish_update(
        self,
        interaction: discord.Interaction,
        消息链接: str
    ):
        await safe_defer(interaction)
        try:
            if not isinstance(interaction.channel, discord.Thread):
                await interaction.followup.send(
                    "❌ 此命令只能在帖子中使用", ephemeral=True
                )
                return
                
            thread = interaction.channel
            if thread.owner_id != interaction.user.id:
                await interaction.followup.send(
                    "❌ 只有贴主才能发布更新", ephemeral=True
                )
                return

            await self.logic.process_publish_update(
                interaction, thread, 消息链接
            )
        except Exception as e:
            logger.error("发布更新命令执行失败", exc_info=True)
            await interaction.followup.send(
                "❌ 发生内部错误，请稍后重试", ephemeral=True
            )

    # @app_commands.command(
    #     name="标签评价", description="对当前帖子的标签进行评价（赞或踩）"
    # )
    async def tag_rate(self, interaction: discord.Interaction):
        await safe_defer(interaction)
        try:
            if not isinstance(interaction.channel, discord.Thread):
                await interaction.followup.send(
                    "此命令只能在帖子中使用。", ephemeral=True
                )
                return

            if not interaction.channel.applied_tags:
                await interaction.followup.send(
                    "该帖子没有应用任何标签。", ephemeral=True
                )
                return

            tag_map = {
                tag.id: tag.name
                for tag in interaction.channel.applied_tags
            }

            view = TagVoteView(
                thread_id=interaction.channel.id,
                thread_name=interaction.channel.name,
                tag_map=tag_map,
                session_factory=self.session_factory,
                api_scheduler=self.bot.api_scheduler,
            )
            # 获取初始统计数据
            async with self.session_factory() as session:
                repo = ThreadRepository(session)
                initial_stats = await repo.get_tag_vote_stats(
                    interaction.channel.id, tag_map
                )

            # 使用初始统计数据创建嵌入
            embed = view.create_embed(initial_stats)

            await interaction.followup.send(
                embed=embed, view=view, ephemeral=True
            )
        except Exception as e:
            error_message = f"❌ 命令执行失败: {e}"
            await interaction.followup.send(
                error_message, ephemeral=True
            )
