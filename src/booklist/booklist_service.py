import logging
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.v1.schemas.booklist.booklist_item_add_data import BooklistItemAddData
from core.booklist_repository import BooklistRepository
from core.redis_trend_service import RedisTrendService
from core.thread_repository import ThreadRepository
from models import BooklistItem, Thread
from shared.enum.constant_enum import ConstantEnum

logger = logging.getLogger(__name__)


class BooklistService:
    """书单业务逻辑服务层，负责协调书单表与帖子表的数据互通"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.booklist_repo = BooklistRepository(session)
        self.thread_repo = ThreadRepository(session)

    async def add_threads(
        self, user_id: int, booklist_id: int, items: List[BooklistItemAddData]
    ) -> List[BooklistItem]:
        """
        将帖子批量加入书单，并同步帖子的被收藏次数（跨书单去重）
        """
        # 权限校验：确认书单存在且属于当前用户
        booklist = await self.booklist_repo.get_booklist(booklist_id)
        if not booklist:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="书单不存在"
            )
        if booklist.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="无权修改此书单"
            )

        # 提取并转换帖子 ID 列表
        thread_ids = [int(item.thread_id) for item in items]
        if not thread_ids:
            return []

        # 查询这些帖子是否已存在于该用户的其他书单中，用于后续净增量计算
        existing_anywhere = await self.booklist_repo.get_threads_in_users_booklists(
            user_id, thread_ids
        )

        # 执行书单添加操作（内部已做该书单内去重）
        added_items = await self.booklist_repo.add_threads_to_booklist(
            booklist_id, items
        )

        if not added_items:
            return []

        # 筛选出全书单级别的净增帖子（排除已在其他书单中存在的帖子）
        added_thread_ids = [item.thread_id for item in added_items]
        net_new_ids = list(
            {tid for tid in added_thread_ids if tid not in existing_anywhere}
        )

        if net_new_ids:
            # 更新数据库中的帖子全局收藏数
            await self.thread_repo.update_collection_counts(net_new_ids, 1)

            # 同步 Redis 飙升榜数据：仅统计近期发布的帖子，防止老帖屠榜
            threshold = datetime.now(timezone.utc) - timedelta(
                days=ConstantEnum.STATISTICS_THRESHOLD_DAYS.value
            )
            stmt = select(Thread.thread_id).where(
                Thread.thread_id.in_(net_new_ids),  # type: ignore
                Thread.created_at >= threshold,
            )
            valid_ids = set((await self.session.execute(stmt)).scalars().all())

            if valid_ids:
                trend_service = RedisTrendService()
                for tid in valid_ids:
                    await trend_service.record_increment("collection", tid, 1)

        return added_items

    async def remove_threads(
        self, user_id: int, booklist_id: int, thread_ids: List[int]
    ) -> int:
        """
        从书单批量移除帖子，并同步帖子的被收藏次数（跨书单去重）
        """
        # 权限校验：确认书单存在且属于当前用户
        booklist = await self.booklist_repo.get_booklist(booklist_id)
        if not booklist:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="书单不存在"
            )
        if booklist.owner_id != user_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="无权修改此书单"
            )

        if not thread_ids:
            return 0

        # 执行书单移除操作
        deleted_count = await self.booklist_repo.remove_threads_from_booklist(
            booklist_id, thread_ids
        )

        if deleted_count > 0:
            # 检查移除后这些帖子是否仍存在于该用户的其他书单中
            still_existing = await self.booklist_repo.get_threads_in_users_booklists(
                user_id, thread_ids
            )

            # 筛选出全书单级别的净减帖子（从所有书单中彻底移除）
            net_removed_ids = list(set(thread_ids) - still_existing)

            if net_removed_ids:
                # 更新数据库中的帖子全局收藏数（仅递减净减项）
                await self.thread_repo.update_collection_counts(net_removed_ids, -1)

        return deleted_count
