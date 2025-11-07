import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.ext.asyncio import async_sessionmaker
from typing import TYPE_CHECKING
import logging
import asyncio

from .batch_service import ThreadMemberBatchService
from .service import ThreadMemberService
from src.auditor.repository import AuditorRepository
from src.shared.safe_defer import safe_defer


if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)

class ThreadMemberCog(commands.Cog, name="ThreadMember"):
    """
    帖子成员管理主模块，负责事件监听和命令处理
    """

    def __init__(self, bot: "MyBot", session_factory: async_sessionmaker):
        """
        初始化主模块

        Args:
            bot: Discord 机器人实例
            session_factory: 数据库会话工厂
        """
        self.bot = bot
        self.session_factory = session_factory
        self.batch_service = ThreadMemberBatchService(session_factory, interval=60)
        logger.info("ThreadMemberCog 模块已加载")

    async def cog_load(self):
        """Cog 加载时启动批量服务"""
        self.batch_service.start()

    async def cog_unload(self):
        """Cog 卸载时停止批量服务"""
        await self.batch_service.stop()
        
    @commands.Cog.listener()
    async def on_thread_member_join(self, member: discord.ThreadMember):
        """
        监听帖子成员加入事件

        Args:
            member: 加入的成员对象
        """
        # 注意: 此事件需要 Intents.members
        await self.batch_service.add_join(member.thread.id, member.id, member.joined_at)

    @commands.Cog.listener()
    async def on_raw_thread_member_remove(self, payload: discord.RawThreadMembersUpdate):
        """
        监听帖子成员离开事件（原始事件）

        Args:
            payload: 原始事件数据
        """
        # 使用 raw 事件更可靠
        # 注意: 此事件需要 Intents.members
        await self.batch_service.add_removal(payload.thread_id, payload.member.id)
        
    @app_commands.command(name="索引帖子成员", description="为服务器上所有帖子建立成员索引")
    @app_commands.default_permissions(administrator=True)
    async def index_thread_members(self, interaction: discord.Interaction):
        """
        执行一次性全量索引：索引服务器上活跃帖子 + thread表中已索引的帖子 - 已索引在ThreadMember表中的帖子
        """
        await safe_defer(interaction, ephemeral=True)
        await interaction.followup.send("即将开始索引所有帖子的成员，这是一个耗时较长的后台任务...", ephemeral=True)

        async def run_full_index():
            try:
                # 1. 获取所有需要扫描的帖子
                async with self.session_factory() as session:
                    thread_repo = AuditorRepository(session)
                    member_service = ThreadMemberService(session)
                    
                    # 获取活跃服务器帖子
                    active_server_threads = {t.id for t in interaction.guild.threads}
                    
                    # 获取数据库中所有已索引的帖子
                    all_db_threads = set(await thread_repo.get_all_thread_ids())
                    
                    # 获取已有成员记录的帖子
                    indexed_member_threads = await member_service.get_indexed_thread_ids()

                # 计算需要扫描的帖子：活跃帖子 + 数据库帖子 - 已有成员记录的帖子
                threads_to_scan = (all_db_threads | active_server_threads) - indexed_member_threads

                logger.info(f"开始全面成员索引，共需扫描 {len(threads_to_scan)} 个帖子。")

                # 2. 逐个扫描 (为避免速率限制，需要加延迟)
                count = 0
                for thread_id in threads_to_scan:
                    try:
                        # 首先尝试从缓存获取帖子
                        channel = self.bot.get_channel(thread_id)
                        
                        # 如果缓存中没有，则从 API 获取
                        if channel is None:
                            channel = await self.bot.fetch_channel(thread_id)
                        
                        if not channel or not isinstance(channel, discord.Thread):
                            continue
                        
                        # 获取帖子成员
                        members = await channel.fetch_members()
                        member_ids = {member.id for member in members}
                        
                        async with self.session_factory() as session:
                            service = ThreadMemberService(session)
                            await service.sync_thread_members(thread_id, member_ids)
                            await session.commit()
                        
                        count += 1
                        if count % 50 == 0:
                            logger.info(f"成员索引进度: {count}/{len(threads_to_scan)}")
                        
                        await asyncio.sleep(2)  # 重要的延迟，避免速率限制
                    except discord.NotFound:
                        logger.warning(f"索引帖子 {thread_id} 成员时找不到帖子，跳过。")
                    except discord.Forbidden:
                        logger.warning(f"索引帖子 {thread_id} 成员时无权限，跳过。")
                    except Exception as e:
                        logger.error(f"索引帖子 {thread_id} 成员时出错: {e}")
                
                logger.info("全面成员索引完成。")
                await interaction.followup.send("✅ 所有帖子的成员索引已完成。", ephemeral=True)

            except Exception as e:
                logger.error(f"全面成员索引任务失败: {e}", exc_info=True)
                await interaction.followup.send("❌ 成员索引任务因严重错误而中止。", ephemeral=True)

        self.bot.loop.create_task(run_full_index())