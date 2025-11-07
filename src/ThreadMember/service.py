import logging
from datetime import datetime, timezone
from typing import List, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, or_
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from ThreadMember.dto import ThreadMemberData
from shared.models.thread_member import ThreadMember

logger = logging.getLogger(__name__)

class ThreadMemberService:
    """
    帖子成员数据服务，负责与 ThreadMember 表的数据库交互。
    """

    def __init__(self, session: AsyncSession):
        """
        初始化服务

        Args:
            session: 异步数据库会话
        """
        self.session = session

    async def batch_add_members(self, members_data: List[ThreadMemberData]):
        """
        批量添加成员，忽略已存在的记录

        Args:
            members_data: 成员数据列表
        """
        if not members_data:
            return
        stmt = sqlite_insert(ThreadMember).values(members_data)
        stmt = stmt.on_conflict_do_nothing(index_elements=["thread_id", "user_id"])
        await self.session.execute(stmt)

    async def batch_remove_members(self, members_data: List[tuple[int, int]]):
        """
        批量移除成员。

        Args:
            members_data: (thread_id, user_id) 元组列表
        """
        if not members_data:
            return
        # SQLAlchemy 2.0+ 的多行删除语法
        where_clauses = [
            (ThreadMember.thread_id == tid) & (ThreadMember.user_id == uid)
            for tid, uid in members_data
        ]
        stmt = delete(ThreadMember).where(or_(*where_clauses))  # type: ignore
        await self.session.execute(stmt)

    async def sync_thread_members(self, thread_id: int, current_member_ids: Set[int]):
        """
        全量同步一个帖子的成员列表，计算差异并更新数据库。

        Args:
            thread_id: 帖子ID
            current_member_ids: 当前帖子中的成员ID集合
        """
        # 获取数据库中该帖子的现有成员
        db_stmt = select(ThreadMember.user_id).where(ThreadMember.thread_id == thread_id)  # type: ignore
        result = await self.session.execute(db_stmt)
        db_member_ids = set(result.scalars().all())

        # 计算差异
        to_add_ids = current_member_ids - db_member_ids
        to_remove_ids = db_member_ids - current_member_ids

        # 执行批量添加和删除
        if to_add_ids:
            await self.batch_add_members([
                {"thread_id": thread_id, "user_id": uid, "joined_at": datetime.now(timezone.utc)}
                for uid in to_add_ids
            ])
        if to_remove_ids:
            await self.batch_remove_members([(thread_id, uid) for uid in to_remove_ids])

        logger.debug(f"同步帖子 {thread_id} 成员: 新增 {len(to_add_ids)}, 移除 {len(to_remove_ids)}")

    async def get_indexed_thread_ids(self) -> Set[int]:
        """
        获取所有已记录成员的帖子ID集合。

        Returns:
            已索引的帖子ID集合
        """
        stmt = select(ThreadMember.thread_id).distinct()  # type: ignore
        result = await self.session.execute(stmt)
        return set(result.scalars().all())