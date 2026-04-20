import asyncio
from datetime import datetime, timedelta, timezone
from shared.redis_client import RedisManager
from shared.enum.constant_enum import ConstantEnum

class RedisTrendService:
    """处理基于Redis的趋势和飙升数据计算并防止缓存击穿"""

    def _get_daily_key(self, metric: str, dt: datetime) -> str:
        """格式化按天分桶的Redis键名"""
        date_str = dt.strftime("%Y%m%d")
        return f"trend:{metric}:{date_str}"

    async def record_increment(self, metric: str, thread_id: int, count: int = 1):
        """记录指定指标的增量并将趋势数据保留九十天"""
        if count <= 0:
            return
            
        redis = RedisManager.get_client()
        now = datetime.now(timezone.utc)
        key = self._get_daily_key(metric, now)
        
        await redis.zincrby(key, count, str(thread_id))
        await redis.expire(key, 86400 * ConstantEnum.MAX_SURGE_DAYS.value)

    async def get_top_surging_ids(self, metric: str, days: int, limit: int) -> list[int]:
        """聚合多天数据带有分布式锁机制以确保高并发性能"""
        redis = RedisManager.get_client()
        cache_key = f"cache:surge:{metric}:{days}"
        
        # 尝试直接命中短效聚合结果缓存
        if await redis.exists(cache_key):
            top_items = await redis.zrevrange(cache_key, 0, limit - 1)
            return [int(item) for item in top_items if item != "-1"]

        lock_key = f"lock:surge:{metric}:{days}"
        acquired = await redis.set(lock_key, "1", ex=30, nx=True)
        
        if acquired:
            try:
                now = datetime.now(timezone.utc)
                keys = [self._get_daily_key(metric, now - timedelta(days=i)) for i in range(days)]
                
                # 执行并集计算将结果写入缓存键
                await redis.zunionstore(cache_key, keys)
                
                # 插入占位符防止因真实结果为空导致的缓存穿透
                if await redis.zcard(cache_key) == 0:
                    await redis.zadd(cache_key, {"-1": 0})
                    
                # 为聚合结果赋予十分钟的生命周期
                await redis.expire(cache_key, ConstantEnum.TREND_CACHE_EXPIRE_SECONDS.value)
                
                top_items = await redis.zrevrange(cache_key, 0, limit - 1)
                return [int(item) for item in top_items if item != "-1"]
            finally:
                # 计算完毕后主动释放计算锁
                await redis.delete(lock_key)
        else:
            # 未拿到计算锁的进程通过轮询等待结果出现
            for _ in range(20):
                await asyncio.sleep(0.15)
                if await redis.exists(cache_key):
                    top_items = await redis.zrevrange(cache_key, 0, limit - 1)
                    return [int(item) for item in top_items if item != "-1"]
                    
            return []