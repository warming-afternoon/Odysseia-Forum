import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.v1.dependencies.security import get_current_user, require_auth
from api.v1.schemas.tags import TagStatsRequest, TagStatsResponse
from core.tag_service import TagService
from shared.database import AsyncSessionFactory

logger = logging.getLogger(__name__)

# 全局依赖，将在 bot_main.py 中被注入
channel_mappings_config: Dict[int, List[Dict]] = {}
async_session_factory: async_sessionmaker | None = None

router = APIRouter(
    prefix="/tags", tags=["标签"], dependencies=[Depends(require_auth)]
)


@router.post(
    "/stats",
    response_model=TagStatsResponse,
    summary="聚合查询标签统计数据",
)
async def stats_tags(
    request: TagStatsRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """一次性聚合获取指定范围内的所有标签使用统计情况"""
    if not async_session_factory:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="数据库会话尚未初始化",
        )

    try:
        async with async_session_factory() as session:
            tag_service = TagService(
                session=session,
                channel_mappings=channel_mappings_config,
            )
            return await tag_service.aggregate_tag_stats(request)
    except Exception as exc:
        logger.error(f"执行标签聚合查询时出错: {exc}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器内部错误，无法获取标签统计信息",
        ) from exc
