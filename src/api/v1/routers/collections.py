"""收藏相关路由"""
import logging
from typing import List, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, status

from shared.database import AsyncSessionFactory
from src.collection.collection_service import CollectionService
from ..dependencies.security import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/collections", tags=["收藏"])


@router.post("/batch-add", summary="批量添加收藏")
async def batch_add_collections(
    thread_ids: List[int],
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    批量添加帖子收藏

    - thread_ids: 帖子ID列表
    """
    try:
        user_id = int(current_user["id"])

        async with AsyncSessionFactory() as session:
            collection_service = CollectionService(session)
            result = await collection_service.add_collections(user_id, thread_ids)

        return {
            "message": "批量收藏操作完成",
            "user_id": user_id,
            "added": result["added"],
            "duplicates": result["duplicates"]
        }

    except Exception as e:
        logger.error(f"批量添加收藏失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="批量添加收藏失败"
        )


@router.post("/batch-remove", summary="批量移除收藏")
async def batch_remove_collections(
    thread_ids: List[int],
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """
    批量移除帖子收藏

    - thread_ids: 帖子ID列表
    """
    try:
        user_id = int(current_user["id"])

        async with AsyncSessionFactory() as session:
            collection_service = CollectionService(session)
            result = await collection_service.remove_collections(user_id, thread_ids)

        return {
            "message": "批量取消收藏操作完成",
            "user_id": user_id,
            "removed": result["removed"],
            "not_found": result["not_found"]
        }

    except Exception as e:
        logger.error(f"批量移除收藏失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="批量移除收藏失败"
        )