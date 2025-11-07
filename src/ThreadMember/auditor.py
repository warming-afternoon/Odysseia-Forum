import asyncio
import logging
import random
import discord
from discord.ext import commands, tasks
from sqlalchemy.ext.asyncio import async_sessionmaker
from .service import ThreadMemberService
from src.auditor.repository import AuditorRepository

logger = logging.getLogger(__name__)

class ThreadMemberAuditor(commands.Cog):
    """
    帖子成员审计器，负责后台审计帖子成员关系
    """

    def __init__(self, bot: commands.Bot, session_factory: async_sessionmaker):
        """
        初始化审计器

        Args:
            bot: Discord 机器人实例
            session_factory: 数据库会话工厂
        """
        self.bot = bot
        self.session_factory = session_factory
        self.audit_queue: list[int] = []
        logger.info("ThreadMemberAuditor 模块已加载")

    async def cog_load(self):
        """Cog 加载时启动审计循环"""
        self.audit_loop.start()

    async def cog_unload(self):
        """Cog 卸载时停止审计循环"""
        self.audit_loop.cancel()

    async def _reload_audit_queue(self):
        """重新加载审计队列"""
        logger.debug("正在为成员审计重新加载帖子队列...")
        async with self.session_factory() as session:
            # 我们需要审计所有已索引的帖子，而不仅仅是已有成员记录的帖子
            repo = AuditorRepository(session)
            self.audit_queue = await repo.get_all_thread_ids()
            random.shuffle(self.audit_queue)
        logger.debug(f"成员审计队列加载完成，共 {len(self.audit_queue)} 个帖子。")

    @tasks.loop(seconds=10)
    async def audit_loop(self):
        """审计循环，每10秒审计一个帖子"""
        if not self.audit_queue:
            await self._reload_audit_queue()
            if not self.audit_queue:
                return  # 数据库为空

        thread_id = self.audit_queue.pop(0)

        try:
            # 首先尝试从缓存获取帖子
            channel = self.bot.get_channel(thread_id)
            
            # 如果缓存中没有，则从 API 获取
            if channel is None:
                channel = await self.bot.fetch_channel(thread_id)
            
            # 确保是 Thread 类型
            if not isinstance(channel, discord.Thread):
                logger.warning(f"成员审计：频道 {thread_id} 不是帖子类型，跳过。")
                return

            # 获取帖子成员
            members = await channel.fetch_members()
            member_ids = {member.id for member in members}
            
            async with self.session_factory() as session:
                service = ThreadMemberService(session)
                await service.sync_thread_members(thread_id, member_ids)
                await session.commit()

        except discord.NotFound:
            logger.warning(f"成员审计：找不到帖子 {thread_id}，跳过。")
        except discord.Forbidden:
            logger.warning(f"成员审计：无权限访问帖子 {thread_id}，跳过。")
        except Exception as e:
            logger.error(f"审计帖子 {thread_id} 的成员时出错: {e}", exc_info=True)

    @audit_loop.before_loop
    async def before_audit_loop(self):
        """审计循环开始前的准备工作"""
        await self.bot.wait_until_ready()
        logger.info("ThreadMemberAuditor 审计循环已启动")