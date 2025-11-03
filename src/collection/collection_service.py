import logging
from typing import List, Dict, Any, Tuple, Sequence
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, and_, select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.sqlite import insert
from shared.models.user_collection import UserCollection
from shared.models.thread_follow import ThreadFollow
from shared.models.thread import Thread

logger = logging.getLogger(__name__)


class CollectionService:
    """封装与帖子收藏相关的数据库操作"""

    def __init__(self, session: AsyncSession):
        self.session = session

    async def add_collection(self, user_id: int, thread_id: int) -> bool:
        """
        为用户添加一个帖子收藏

        Returns:
            bool: 如果是新收藏则返回 True，如果已存在则返回 False
        """
        new_collection = UserCollection(user_id=user_id, thread_id=thread_id)
        self.session.add(new_collection)
        try:
            await self.session.commit()
            return True
        except IntegrityError:
            # 触发了 UNIQUE 约束
            logger.debug(f"用户 {user_id} 尝试收藏已存在的帖子 {thread_id}")
            await self.session.rollback()
            return False
        except Exception as e:
            logger.error(
                f"为用户 {user_id} 添加收藏 {thread_id} 时出错: {e}", exc_info=True
            )
            await self.session.rollback()
            raise

    async def remove_collection(self, user_id: int, thread_id: int) -> bool:
        """
        为用户移除一个帖子收藏。

        Returns:
            bool: 如果成功移除了记录则返回 True，如果记录不存在则返回 False。
        """
        statement = delete(UserCollection).where(
            and_(
                UserCollection.user_id == user_id, UserCollection.thread_id == thread_id
            )
        )
        result = await self.session.execute(statement)
        await self.session.commit()

        if result.rowcount > 0:
            return True
        else:
            logger.debug(f"用户 {user_id} 尝试移除不存在的收藏 {thread_id}")
            return False

    async def add_collections(self, user_id: int, thread_ids: List[int]) -> Dict[str, Any]:
        """
        批量添加帖子收藏。

        Args:
            user_id: 用户ID
            thread_ids: 帖子ID列表

        Returns:
            包含成功添加数量和重复数量的字典。
        """
        if not thread_ids:
            return {"added": 0, "duplicates": 0}

        # 构建插入值列表
        values = [
            {"user_id": user_id, "thread_id": tid}
            for tid in thread_ids
        ]
        stmt = (
            insert(UserCollection)
            .values(values)
            .on_conflict_do_nothing(index_elements=["user_id", "thread_id"])
        )
        result = await self.session.execute(stmt)
        await self.session.commit()
        # 获取插入的行数
        added = result.rowcount if result.rowcount is not None else 0
        duplicates = len(thread_ids) - added
        return {"added": added, "duplicates": duplicates}

    async def remove_collections(self, user_id: int, thread_ids: List[int]) -> Dict[str, Any]:
        """
        批量移除帖子收藏。

        Args:
            user_id: 用户ID
            thread_ids: 帖子ID列表

        Returns:
            包含成功移除数量和未找到数量的字典。
        """
        if not thread_ids:
            return {"removed": 0, "not_found": 0}

        statement = delete(UserCollection).where(
            and_(
                UserCollection.user_id == user_id,
                UserCollection.thread_id.in_(thread_ids)  # type: ignore
            )
        )
        result = await self.session.execute(statement)
        await self.session.commit()
        removed = result.rowcount
        not_found = len(thread_ids) - removed
        return {"removed": removed, "not_found": not_found}

    async def get_followed_not_collected_threads(
        self, user_id: int, page: int, per_page: int
    ) -> Tuple[List[Thread], int]:
        """获取用户已关注但未收藏的帖子"""
        offset = (page - 1) * per_page

        # 子查询：获取用户已收藏的帖子ID
        collected_subquery = select(UserCollection.thread_id).where(
            UserCollection.user_id == user_id
        )

        # 主查询
        base_query = (
            select(Thread)
            .join(ThreadFollow, Thread.thread_id == ThreadFollow.thread_id)  # type: ignore
            .where(
                ThreadFollow.user_id == user_id,
                Thread.thread_id.not_in(collected_subquery),  # type: ignore
            )
        )

        # 计数
        count_stmt = select(func.count()).select_from(base_query.alias("sub"))
        count_result = await self.session.execute(count_stmt)
        total_count = count_result.scalar_one_or_none() or 0

        # 获取数据
        data_stmt = base_query.order_by(Thread.title).offset(offset).limit(per_page)
        data_result = await self.session.execute(data_stmt)
        threads = data_result.scalars().all()

        return list(threads), total_count

    async def get_collected_threads(
        self, user_id: int, page: int, per_page: int
    ) -> Tuple[List[Thread], int]:
        """获取用户已收藏的帖子"""
        offset = (page - 1) * per_page

        base_query = (
            select(Thread)
            .join(UserCollection, Thread.thread_id == UserCollection.thread_id)  # type: ignore
            .where(UserCollection.user_id == user_id)
        )

        # 计数
        count_stmt = select(func.count()).select_from(base_query.alias("sub"))
        count_result = await self.session.execute(count_stmt)
        total_count = count_result.scalar_one_or_none() or 0

        # 获取数据
        data_stmt = base_query.order_by(Thread.title).offset(offset).limit(per_page)
        data_result = await self.session.execute(data_stmt)
        threads = data_result.scalars().all()

        return list(threads), total_count

    async def get_collected_thread_ids(
        self, user_id: int, thread_ids: Sequence[int]
    ) -> set[int]:
        """
        从给定的帖子ID列表中，找出指定用户已收藏的帖子ID。

        Args:
            user_id: 用户ID
            thread_ids: 要检查的帖子ID列表

        Returns:
            一个包含已收藏帖子ID的集合。
        """
        if not thread_ids:
            return set()

        statement = select(UserCollection.thread_id).where(
            UserCollection.user_id == user_id,
            UserCollection.thread_id.in_(thread_ids),  # type: ignore
        )
        result = await self.session.execute(statement)
        return set(result.scalars().all())
