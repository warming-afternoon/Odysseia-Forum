import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.v1.dependencies.security import get_current_user, require_auth
from api.v1.schemas.tags import TagStatsRequest, TagStatsResponse
from tag.tag_statistics_service import TagStatisticsService
from core.cache_service import CacheService

logger = logging.getLogger(__name__)

# 全局依赖，将在 bot_main.py 中被注入
channel_mappings_config: Dict[int, List[Dict]] = {}
async_session_factory: async_sessionmaker | None = None
cache_service_instance: Optional[CacheService] = None

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
    """
    一次性聚合获取指定范围内的所有标签使用统计情况
    """
    if not async_session_factory or not cache_service_instance:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="核心服务尚未初始化"
        )

    try:
        async with async_session_factory() as session:
            tag_service = TagStatisticsService(
                session=session,
                cache_service=cache_service_instance,
                channel_mappings=channel_mappings_config
            )
            return await tag_service.aggregate_tag_stats(request)
            
    except Exception as e:
        logger.error(f"执行标签聚合查询时出错: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器内部错误，无法获取标签统计信息"
        )
