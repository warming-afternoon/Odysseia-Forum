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
from core.thread_repository import ThreadRepository
from shared.enum.collection_type import CollectionType

logger = logging.getLogger(__name__)

async_session_factory: Optional[async_sessionmaker] = None
main_guild_id: int = 0  # 注入的主服务器 ID

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
            prefs = await pref_repo.get_user_preferences(user_id, main_guild_id)

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

@router.get("/random", response_model=List[ThreadDetail], summary="获取随机帖子")
async def get_random_threads(
    limit: int = Query(default=10, ge=1, le=50, description="抽取数量"),
    channel_ids: Optional[List[int]] = Query(default=None, description="频道筛选范围"),
    include_tags: Optional[List[str]] = Query(default=None, description="包含的标签名"),
    exclude_tags: Optional[List[str]] = Query(default=None, description="必须排除的标签名"),
    tag_logic: str = Query(default="and", description="标签逻辑，'and' 表示必须包含所有标签，'or' 表示包含任意标签"),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """根据指定范围随机抽取帖子"""
    # 检查数据库服务是否就绪
    if not async_session_factory:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="数据库尚未初始化")

    # 获取当前请求的用户标识
    user_id = int(current_user["id"]) if current_user and "id" in current_user else None

    try:
        async with async_session_factory() as session:
            repo = ThreadRepository(session)

            # 从数据库中获取随机抽取的帖子
            threads = await repo.get_random_threads(
                limit=limit,
                channel_ids=channel_ids,
                include_tags=include_tags,
                exclude_tags=exclude_tags,
                tag_logic=tag_logic,
            )

            # 准备集合用于存放用户已收藏的帖子标识
            collected_ids: Set[int] = set()

            # 若用户已登录且查出结果则批量查询收藏状态
            if user_id and threads:
                thread_ids = [t.thread_id for t in threads]
                coll_repo = CollectionRepository(session)
                collected_ids = await coll_repo.get_collected_target_ids(
                    user_id, CollectionType.THREAD, thread_ids
                )

            # 构建并返回包含收藏状态的帖子详情响应模型
            return [_build_thread_detail(t, collected_ids) for t in threads]
            
    except Exception as e:
        logger.error(f"获取随机帖子失败: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取随机帖子发生异常")
