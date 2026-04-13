import logging
from typing import Any, Dict, Optional, Set, List
from fastapi import APIRouter, Depends, Query, HTTPException, status
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.v1.dependencies.security import get_current_user, require_auth
from api.v1.schemas.discovery import DiscoveryRailsResponse
from api.v1.schemas.search.thread_detail import ThreadDetail
from api.v1.schemas.search.author import AuthorDetail
from discovery.discovery_service import DiscoveryService
from core.preferences_repository import PreferencesRepository
from core.collection_repository import CollectionRepository
from shared.enum.collection_type import CollectionType

logger = logging.getLogger(__name__)

async_session_factory: Optional[async_sessionmaker] = None

router = APIRouter(prefix="/discovery", tags=["发现"], dependencies=[Depends(require_auth)])


def _build_thread_detail(thread, collected_ids: Set[int]) -> ThreadDetail:
    """将ORM模型转为前端展示对象"""
    return ThreadDetail(
        thread_id=thread.thread_id,
        guild_id=thread.guild_id,
        channel_id=thread.channel_id,
        title=thread.title,
        author=AuthorDetail.model_validate(thread.author) if thread.author else None,
        created_at=thread.created_at,
        last_active_at=thread.last_active_at,
        reaction_count=thread.reaction_count,
        reply_count=thread.reply_count,
        display_count=thread.display_count,
        first_message_excerpt=thread.first_message_excerpt,
        thumbnail_urls=thread.thumbnail_urls or [],
        tags=[tag.name for tag in thread.tags],
        virtual_tags=[],
        collected_flag=thread.thread_id in collected_ids
    )


@router.get("/rails", response_model=DiscoveryRailsResponse, summary="获取广场轨道数据")
async def get_discovery_rails(
    limit: int = Query(default=10, ge=1, le=50, description="每条轨道返回的数量"),
    days: int = Query(default=30, ge=1, le=90, description="统计时间跨度(天数)"),
    apply_preferences: bool = Query(default=True, description="是否应用当前用户的过滤偏好"),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """一次性获取多条轨道数据并处理收藏标记"""
    if not async_session_factory:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="数据库尚未初始化")

    user_id = int(current_user["id"]) if current_user and "id" in current_user else None
    prefs = None
    
    # 获取用户偏好设置
    if apply_preferences and user_id:
        async with async_session_factory() as session:
            pref_repo = PreferencesRepository(session)
            prefs = await pref_repo.get_user_preferences(user_id, 0)

    try:
        async with async_session_factory() as session:
            service = DiscoveryService(session)
            # 获取四条轨道的原始数据
            rails_data = await service.get_discovery_rails(limit, days, prefs)

            # 汇总所有轨道中出现的帖子ID以便批量查询收藏状态
            all_threads = []
            for rail_list in rails_data.values():
                all_threads.extend(rail_list)
            
            all_ids = [t.thread_id for t in all_threads]
            collected_ids: Set[int] = set()

            # 批量获取当前用户的收藏状态
            if user_id and all_ids:
                coll_repo = CollectionRepository(session)
                collected_ids = await coll_repo.get_collected_target_ids(
                    user_id, CollectionType.THREAD, all_ids
                )

            # 将结果转换为前端 Schema 对象并注入收藏状态
            return DiscoveryRailsResponse(
                latest=[_build_thread_detail(t, collected_ids) for t in rails_data["latest"]],
                reaction_surge=[_build_thread_detail(t, collected_ids) for t in rails_data["reaction_surge"]],
                discussion_surge=[_build_thread_detail(t, collected_ids) for t in rails_data["discussion_surge"]],
                collection_surge=[_build_thread_detail(t, collected_ids) for t in rails_data["collection_surge"]],
            )
    except Exception as e:
        logger.error(f"获取广场轨道数据失败: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取轨道数据发生异常")
