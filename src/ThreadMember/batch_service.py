import asyncio
import logging
from collections import defaultdict
from datetime import datetime
from sqlalchemy.ext.asyncio import async_sessionmaker
from .service import ThreadMemberService

logger = logging.getLogger(__name__)

class ThreadMemberBatchService:
    """
    负责批量更新帖子成员加入和离开记录的服务。
    """

    def __init__(self, session_factory: async_sessionmaker, interval: int = 60):
        """
        初始化批量更新帖子成员服务

        Args:
            session_factory: 数据库会话工厂
            interval: 批量写入间隔（秒），默认60秒
        """
        self.session_factory = session_factory
        self.interval = interval
        self.pending_joins = set()  # 使用 set 自动去重, 存储 (thread_id, user_id, datetime)
        self.pending_removals = set() # 存储 (thread_id, user_id)
        self.lock = asyncio.Lock()
        self._task: asyncio.Task | None = None

    def start(self):
        """启动批量处理循环"""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self):
        """停止批量处理循环并刷新剩余数据"""
        if self._task and not self._task.done():
            self._task.cancel()
        await self.flush_to_db()

    async def add_join(self, thread_id: int, user_id: int, joined_at: datetime):
        """
        添加成员加入记录到缓存

        Args:
            thread_id: 帖子ID
            user_id: 用户ID
            joined_at: 加入时间
        """
        async with self.lock:
            # 如果同一个用户先离开后加入，移除离开记录
            if (thread_id, user_id) in self.pending_removals:
                self.pending_removals.remove((thread_id, user_id))
            self.pending_joins.add((thread_id, user_id, joined_at))

    async def add_removal(self, thread_id: int, user_id: int):
        """
        添加成员离开记录到缓存

        Args:
            thread_id: 帖子ID
            user_id: 用户ID
        """
        async with self.lock:
            # 如果同一个用户先加入后离开，移除加入记录
            join_record = next((j for j in self.pending_joins if j[0] == thread_id and j[1] == user_id), None)
            if join_record:
                self.pending_joins.remove(join_record)
            self.pending_removals.add((thread_id, user_id))

    async def flush_to_db(self):
        """将缓存中的成员变动批量写入数据库"""
        async with self.lock:
            if not self.pending_joins and not self.pending_removals:
                return

            joins_to_process = self.pending_joins.copy()
            removals_to_process = self.pending_removals.copy()
            self.pending_joins.clear()
            self.pending_removals.clear()

        if not joins_to_process and not removals_to_process:
            return

        try:
            async with self.session_factory() as session:
                service = ThreadMemberService(session)
                if joins_to_process:
                    await service.batch_add_members([
                        {"thread_id": tid, "user_id": uid, "joined_at": jat}
                        for tid, uid, jat in joins_to_process
                    ])
                if removals_to_process:
                    await service.batch_remove_members(list(removals_to_process))
                await session.commit()
            logger.debug(f"批量更新帖子成员: {len(joins_to_process)} 个加入, {len(removals_to_process)} 个离开。")
        except Exception as e:
            logger.error(f"批量更新帖子成员失败: {e}", exc_info=True)
            # 失败后将数据还回缓存，下次重试
            async with self.lock:
                self.pending_joins.update(joins_to_process)
                self.pending_removals.update(removals_to_process)

    async def _run_loop(self):
        """批量处理循环"""
        while True:
            await asyncio.sleep(self.interval)
            await self.flush_to_db()