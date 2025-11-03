import logging
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import delete, and_
from sqlalchemy.exc import IntegrityError
from shared.models.user_collection import UserCollection

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
