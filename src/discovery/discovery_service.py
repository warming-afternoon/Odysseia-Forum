from typing import Dict, List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from core.redis_trend_service import RedisTrendService
from discovery.discovery_repository import DiscoveryRepository
from dto.preferences.user_search_preferences_dto import UserSearchPreferencesDTO
from models import Thread

class DiscoveryService:
    """编排并整合多条轨道的发现页服务"""

    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = DiscoveryRepository(session)
        self.trend_service = RedisTrendService()

    async def _get_latest_threads_with_retry(self, limit: int, prefs: Optional[UserSearchPreferencesDTO]) -> List[Thread]:
        """获取最新轨道"""
        result_threads = []
        # 一次多查些去过滤
        batch_size = limit * 2 
        
        # 最多重复2次补充（共尝试3次）
        for attempt in range(3):
            needed = limit - len(result_threads)
            if needed <= 0:
                break
            
            offset = attempt * batch_size
            threads = await self.repo.get_latest_threads(batch_size, offset, prefs)
            if not threads:
                break  # 数据库这部分没有数据了
                
            for t in threads:
                if t.thread_id not in [rt.thread_id for rt in result_threads]:
                    result_threads.append(t)
                    if len(result_threads) >= limit:
                        break
            
            # 拿到少于批次上限意味着后面没有新的内容了，停止查询
            if len(threads) < batch_size:
                break
                
        return result_threads[:limit]

    async def _get_surge_threads_with_retry(self, metric: str, days: int, limit: int, prefs: Optional[UserSearchPreferencesDTO]) -> List[Thread]:
        """获取飙升轨道数据"""
        result_threads = []
        # 考虑到飙升轨道应用过滤后数据衰减可能更厉害，单次由Redis获取3倍数量
        batch_size = limit * 3
        
        # 最多重复2次补充（共尝试3次）
        for attempt in range(3):
            needed = limit - len(result_threads)
            if needed <= 0:
                break
                
            offset = attempt * batch_size
            # 带上 offset 去 Redis 请求排名继续顺延的下一段 ID
            ids = await self.trend_service.get_top_surging_ids(metric, days, batch_size, offset)
            if not ids:
                break
                
            threads = await self.repo.get_threads_by_ids_ordered(ids, prefs)
            
            # 由于可能在 DB 层面因为 FTS、Tag 等过滤掉了部分，这里追加至数组（本身保持 Redis 里的顺序）
            for t in threads:
                if t.thread_id not in [rt.thread_id for rt in result_threads]:
                    result_threads.append(t)
                    if len(result_threads) >= limit:
                        break
                        
            # 如果 Redis 返回的 id 数不到请求的批量，说明榜单没数据了，提早中断
            if len(ids) < batch_size:
                break
                
        return result_threads[:limit]

    async def get_discovery_rails(self, limit_per_rail: int, days: int, prefs: Optional[UserSearchPreferencesDTO]) -> Dict[str, List[Thread]]:
        """获取所有四条轨道数据，应用偏好并在数量不足时进行最多2次的补偿请求"""
        
        latest_threads = await self._get_latest_threads_with_retry(limit_per_rail, prefs)
        reaction_threads = await self._get_surge_threads_with_retry("reaction", days, limit_per_rail, prefs)
        discussion_threads = await self._get_surge_threads_with_retry("reply", days, limit_per_rail, prefs)
        collection_threads = await self._get_surge_threads_with_retry("collection", days, limit_per_rail, prefs)
        
        return {
            "latest": latest_threads,
            "reaction_surge": reaction_threads,
            "discussion_surge": discussion_threads,
            "collection_surge": collection_threads
        }