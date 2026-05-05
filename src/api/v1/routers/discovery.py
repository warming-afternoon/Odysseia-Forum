import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.v1.dependencies.security import get_current_user, require_auth
from api.v1.schemas.discovery import DiscoveryRailsResponse
from api.v1.schemas.search import ThreadDetail
from api.v1.utils import ThreadDetailBuilder
from core.collection_repository import CollectionRepository
from core.preferences_repository import PreferencesRepository
from core.thread_repository import ThreadRepository
from discovery.discovery_service import DiscoveryService
from shared.enum.collection_type import CollectionType

logger = logging.getLogger(__name__)

async_session_factory: Optional[async_sessionmaker] = None
main_guild_id: int = 0  # 注入的主服务器 ID
channel_mappings_config: Dict[int, List[Dict]] = {}  # 注入的频道映射配置

router = APIRouter(prefix="/discovery", tags=["发现"], dependencies=[Depends(require_auth)])


@router.get("/rails", response_model=DiscoveryRailsResponse, summary="获取广场轨道数据")
async def get_discovery_rails(
    limit: int = Query(default=10, ge=1, le=50, description="每条轨道返回的数量"),
    days: int = Query(default=30, ge=1, le=90, description="统计时间跨度(天数)"),
    apply_preferences: bool = Query(default=True, description="是否应用当前用户的过滤偏好"),
    current_user: Dict[str, Any] = Depends(get_current_user)
):
    """一次性获取多条轨道数据并处理收藏标记和虚拟标签"""
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

            # 汇总所有轨道中出现的帖子 ID 以便批量查询收藏状态
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

            # 初始化构造器
            builder = ThreadDetailBuilder(channel_mappings_config)

            # 将结果转换为前端 Schema 对象并注入收藏状态与虚拟标签
            return DiscoveryRailsResponse(
                latest=builder.build_list(rails_data["latest"], collected_ids),
                reaction_surge=builder.build_list(rails_data["reaction_surge"], collected_ids),
                discussion_surge=builder.build_list(rails_data["discussion_surge"], collected_ids),
                collection_surge=builder.build_list(rails_data["collection_surge"], collected_ids),
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
    """根据指定范围随机抽取帖子，包含虚拟标签"""
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

            builder = ThreadDetailBuilder(channel_mappings_config)
            return builder.build_list(threads, collected_ids)
            
    except Exception as e:
        logger.error(f"获取随机帖子失败: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="获取随机帖子发生异常")
