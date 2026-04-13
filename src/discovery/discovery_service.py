from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from discovery.redis_trend_service import RedisTrendService
from discovery.discovery_repository import DiscoveryRepository
from dto.preferences.user_search_preferences import UserSearchPreferencesDTO
from models import Thread

class DiscoveryService:
    """编排并整合多条轨道的发现页服务"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = DiscoveryRepository(session)
        self.trend_service = RedisTrendService()

    async def get_discovery_rails(self, limit_per_rail: int, days: int, prefs: Optional[UserSearchPreferencesDTO]) -> Dict[str, List[Thread]]:
        """获取所有规划好的轨道数据"""
        latest_threads = await self.repo.get_latest_threads(limit_per_rail, prefs)
        
        # 为了应用偏好后还能剩够数量先查询四倍的ID
        query_multiplier = 4

        reaction_ids = await self.trend_service.get_top_surging_ids("reaction", days, limit_per_rail * query_multiplier)
        reaction_threads = await self.repo.get_threads_by_ids_ordered(reaction_ids, prefs)
        reaction_threads = reaction_threads[:limit_per_rail]
        
        discussion_ids = await self.trend_service.get_top_surging_ids("reply", days, limit_per_rail * query_multiplier)
        discussion_threads = await self.repo.get_threads_by_ids_ordered(discussion_ids, prefs)
        discussion_threads = discussion_threads[:limit_per_rail]
        
        collection_ids = await self.trend_service.get_top_surging_ids("collection", days, limit_per_rail * query_multiplier)
        collection_threads = await self.repo.get_threads_by_ids_ordered(collection_ids, prefs)
        collection_threads = collection_threads[:limit_per_rail]
        
        return {
            "latest": latest_threads,
            "reaction_surge": reaction_threads,
            "discussion_surge": discussion_threads,
            "collection_surge": collection_threads
        }