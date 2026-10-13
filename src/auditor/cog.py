import asyncio
import logging
import random
import time
from typing import TYPE_CHECKING

from discord.ext import commands, tasks
from sqlalchemy.ext.asyncio import async_sessionmaker

from auditor.auditor_service import AuditorService

if TYPE_CHECKING:
    from bot_main import MyBot

logger = logging.getLogger(__name__)


class Auditor(commands.Cog):
    """通过独立游标分别审计频道和消息，逐批覆盖全部已入库帖子。"""

    AUDIT_BATCH_SIZE = 500
    CHANNEL_SLEEP_SECONDS = 3.0  # 每次频道审计尝试结束后的等待时间（秒）。
    MESSAGE_SLEEP_SECONDS = 0.5  # 每次消息审计尝试结束后的等待时间（秒）。
    CYCLE_SLEEP_SECONDS = 60.0  # 整轮审计结束或批次读取失败后的等待时间（秒）。
    MESSAGE_WARN_SECONDS = 18 * 3600  # 消息审计整轮耗时达到 18 小时的预警阈值（秒）。
    MESSAGE_ALERT_SECONDS = 24 * 3600  # 消息审计整轮耗时超过 24 小时的告警阈值（秒）。

    def __init__(self, bot: "MyBot", session_factory: async_sessionmaker):
        """初始化两个独立的分页游标和周期统计。"""
        self.bot = bot
        self.session_factory = session_factory
        self.sync_service = bot.sync_service
        self._channel_cursor = 0
        self._message_cursor = 0
        self._cycle_started: dict[str, float] = {}
        self._cycle_counts: dict[str, dict[str, int]] = {}
        self._message_warning_level = 0
        logger.info("Auditor 模块已加载")

    async def cog_load(self):
        """启动频道审计、消息审计及原有清理任务。"""
        self.channel_audit_loop.start()
        self.message_audit_loop.start()
        self.cleanup_loop.start()

    async def cog_unload(self):
        """同时取消所有后台审计任务。"""
        self.channel_audit_loop.cancel()
        self.message_audit_loop.cancel()
        self.cleanup_loop.cancel()

    @tasks.loop(seconds=0)
    async def channel_audit_loop(self):
        """逐帖同步频道数据，每次尝试后休眠三秒。"""
        await self._run_batch("channel")

    @tasks.loop(seconds=0)
    async def message_audit_loop(self):
        """逐帖同步消息数据，每次尝试后休眠半秒。"""
        await self._run_batch("message")

    def _check_message_cycle_duration(self):
        """消息周期达到十八小时预警，超过一天告警，每级仅记录一次。"""
        started = self._cycle_started.get("message")
        if started is None:
            return
        elapsed = time.monotonic() - started
        if elapsed > self.MESSAGE_ALERT_SECONDS and self._message_warning_level < 2:
            self._message_warning_level = 2
            logger.error("消息审计周期已超过24小时: elapsed_seconds=%.1f", elapsed)
        elif elapsed >= self.MESSAGE_WARN_SECONDS and self._message_warning_level < 1:
            self._message_warning_level = 1
            logger.warning("消息审计周期已达到18小时: elapsed_seconds=%.1f", elapsed)

    async def _run_batch(self, kind: str):
        """共用批处理机制，隔离游标、单帖错误和整轮统计。"""
        cursor_attr = f"_{kind}_cursor"
        loop = self.channel_audit_loop if kind == "channel" else self.message_audit_loop
        sync = (
            self.sync_service.sync_thread_channel
            if kind == "channel"
            else self.sync_service.sync_thread_messages
        )
        delay = (
            self.CHANNEL_SLEEP_SECONDS
            if kind == "channel"
            else self.MESSAGE_SLEEP_SECONDS
        )
        if loop.is_being_cancelled():
            return
        if kind not in self._cycle_started:
            self._cycle_started[kind] = time.monotonic()
            self._cycle_counts[kind] = {"processed": 0, "success": 0, "failed": 0}
            if kind == "message":
                self._message_warning_level = 0
            logger.info("开始后台审计周期: kind=%s", kind)
        if kind == "message":
            self._check_message_cycle_duration()

        # 不在读取 Discord 数据期间持有分页查询会话。
        try:
            async with self.session_factory() as session:
                batch = await AuditorService(session).get_thread_ids_batch(
                    cursor=getattr(self, cursor_attr), batch_size=self.AUDIT_BATCH_SIZE
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("审计批次读取失败: kind=%s", kind)
            await asyncio.sleep(self.CYCLE_SLEEP_SECONDS)
            return

        if not batch:
            elapsed = time.monotonic() - self._cycle_started[kind]
            counts = self._cycle_counts.pop(kind)
            logger.info(
                "后台审计周期完成: kind=%s elapsed_seconds=%.1f processed=%s success=%s failed=%s",
                kind,
                elapsed,
                counts["processed"],
                counts["success"],
                counts["failed"],
            )
            self._cycle_started.pop(kind)
            setattr(self, cursor_attr, 0)
            await asyncio.sleep(self.CYCLE_SLEEP_SECONDS)
            return

        thread_ids = [thread_id for _, thread_id in batch]
        random.shuffle(thread_ids)
        for thread_id in thread_ids:
            if loop.is_being_cancelled():
                return
            try:
                # 完整业务流程不提交调度器，各同步入口只调度具体 HTTP 请求。
                result = await sync(thread_id, priority=10)
                success = result.success
                if not success:
                    logger.warning(
                        "单帖审计失败: kind=%s thread_id=%s error_code=%s",
                        kind,
                        thread_id,
                        result.error_code,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                success = False
                logger.exception("单帖审计异常: kind=%s thread_id=%s", kind, thread_id)
            counts = self._cycle_counts[kind]
            counts["processed"] += 1
            counts["success" if success else "failed"] += 1
            if kind == "message":
                self._check_message_cycle_duration()
            # 单帖失败也休眠，避免异常路径变成无间隔请求循环。
            await asyncio.sleep(delay)

        setattr(self, cursor_attr, batch[-1][0])
        logger.debug(
            "审计批次完成: kind=%s cursor=%s size=%s", kind, batch[-1][0], len(batch)
        )

    @tasks.loop(hours=6)
    async def cleanup_loop(self):
        """定期清理被多次确认不存在的帖子。"""
        try:
            async with self.session_factory() as session:
                deleted_count = await AuditorService(session).delete_stale_threads(
                    threshold=5
                )
            logger.info("幽灵数据清理完成，共删除了 %s 条记录。", deleted_count)
        except Exception:
            logger.exception("幽灵数据清理任务发生错误")

    @channel_audit_loop.before_loop
    @message_audit_loop.before_loop
    @cleanup_loop.before_loop
    async def before_loops(self):
        """在所有循环开始前等待机器人准备就绪。"""
        await self.bot.wait_until_ready()
