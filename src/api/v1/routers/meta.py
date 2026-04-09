from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.v1.dependencies.security import get_current_user
from dto.meta import ChannelDetail
from core.cache_service import CacheService
from core.meta_service import MetaService
from shared.database import AsyncSessionFactory
# 导入配置类型枚举
from shared.enum.search_config_type import SearchConfigType

# 全局依赖，将在 bot_main.py 中被注入
cache_service_instance: Optional[CacheService] = None
channel_mappings_config: Dict[int, List[Dict]] = {}


router = APIRouter(
    prefix="/meta", tags=["元数据"], dependencies=[Depends(get_current_user)]
)


@router.get(
    "/channels", response_model=List[ChannelDetail], summary="获取频道目录与基础信息"
)
async def get_indexed_channels_with_tags(
    channel_ids: Optional[List[int]] = Query(
        default=None, description="要查询的特定频道ID列表"
    ),
    guild_id: Optional[int] = Query(
        default=None, description="按服务器ID过滤频道"
    ),
):
    """返回指定频道的标签、虚拟映射及发帖统计量"""
    if not cache_service_instance:
        raise HTTPException(status_code=503, detail="Cache 服务尚未初始化")

    async with AsyncSessionFactory() as session:
        meta_service = MetaService(
            session=session,
            cache_service=cache_service_instance,
            channel_mappings=channel_mappings_config,
        )
        return await meta_service.get_channels_meta(guild_id, channel_ids)

@router.get(
    "/main-guild",
    summary="获取当前主服务器ID"
)
async def get_main_guild_id():
    """返回配置文件中定义的主服务器 ID (Main Guild ID)"""
    if not cache_service_instance:
        raise HTTPException(status_code=503, detail="Cache 服务尚未初始化")
    
    # 从缓存中获取主服务器配置
    config = await cache_service_instance.get_bot_config(SearchConfigType.MAIN_GUILD_ID)
    
    if not config or config.value_int is None:
        # 如果数据库中没找到，理论上不应该发生，因为 bot_main 会初始化它
        return {"main_guild_id": "0"}

    # 将 ID 转换为字符串返回，防止前端 JavaScript 丢失大整数精度
    return {"main_guild_id": str(config.value_int)}
