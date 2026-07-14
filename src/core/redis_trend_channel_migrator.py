import json
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable, Sequence
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlmodel import select

from core.redis_trend_service import RedisTrendService
from models import Thread
from shared.enum import ConstantEnum


class RedisTrendChannelMigrator:
    """将全局趋势日榜幂等回填为按频道拆分的日榜。"""

    LOCK_KEY = "migration:trend-channel:v1:lock"
    PROGRESS_KEY = "migration:trend-channel:v1:progress"
    LOCK_TTL_SECONDS = 300
    DEFAULT_METRICS = ("reaction", "reply", "collection")
    ZADD_IF_GREATER_SCRIPT = """
    local updated = 0
    for index = 1, #ARGV, 2 do
        local score = tonumber(ARGV[index])
        local member = ARGV[index + 1]
        local current = redis.call('zscore', KEYS[1], member)
        if (not current) or score > tonumber(current) then
            redis.call('zadd', KEYS[1], score, member)
            updated = updated + 1
        end
    end
    return updated
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis_client,
        metrics: Sequence[str] = DEFAULT_METRICS,
        max_days: int = ConstantEnum.MAX_SURGE_DAYS.value,
        batch_size: int = 500,
    ):
        self.session_factory = session_factory
        self.redis = redis_client
        self.metrics = tuple(metrics)
        self.max_days = max_days
        self.batch_size = batch_size
        self.trend_service = RedisTrendService()

    async def run(
        self, force: bool = False, now: datetime | None = None
    ) -> dict[str, int | str]:
        """执行或恢复回填，并在全部日榜校验成功后写入完成标记。"""
        if not force:
            completed = await self.redis.get(
                RedisTrendService.CHANNEL_MIGRATION_COMPLETE_KEY
            )
            if completed:
                return json.loads(completed)

        lock_token = uuid4().hex
        acquired = await self.redis.set(
            self.LOCK_KEY,
            lock_token,
            ex=self.LOCK_TTL_SECONDS,
            nx=True,
        )
        if not acquired:
            raise RuntimeError("频道趋势回填正在由其他进程执行")

        try:
            if force:
                await self.redis.delete(
                    self.PROGRESS_KEY,
                    RedisTrendService.CHANNEL_MIGRATION_COMPLETE_KEY,
                )

            migration_now = now or datetime.now(timezone.utc)
            totals = {
                "source_keys": 0,
                "source_members": 0,
                "migrated_members": 0,
                "unmapped_members": 0,
            }

            for metric in self.metrics:
                for day_index in range(self.max_days):
                    day = migration_now - timedelta(days=day_index)
                    source_key = self.trend_service._get_daily_key(metric, day)
                    saved_result = await self.redis.hget(
                        self.PROGRESS_KEY, source_key
                    )
                    if saved_result:
                        result = json.loads(saved_result)
                    else:
                        result = await self._migrate_source_key(
                            metric, day, source_key, lock_token
                        )

                    totals["source_keys"] += 1
                    totals["source_members"] += result["source_members"]
                    totals["migrated_members"] += result["migrated_members"]
                    totals["unmapped_members"] += result["unmapped_members"]

            if (
                totals["migrated_members"] + totals["unmapped_members"]
                != totals["source_members"]
            ):
                raise RuntimeError("频道趋势回填校验失败，未写入完成标记")

            # 清除可能存在的旧频道聚合缓存，确保切换后读取回填完成的数据。
            await self._clear_channel_aggregate_caches()
            completed_result: dict[str, int | str] = {
                **totals,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
            await self.redis.set(
                RedisTrendService.CHANNEL_MIGRATION_COMPLETE_KEY,
                json.dumps(completed_result, ensure_ascii=False),
            )
            return completed_result
        finally:
            await self._release_lock(lock_token)

    async def _migrate_source_key(
        self,
        metric: str,
        day: datetime,
        source_key: str,
        lock_token: str,
    ) -> dict[str, int]:
        """分批迁移单个全局日榜并记录可恢复进度。"""
        source_items = await self.redis.zrange(
            source_key, 0, -1, withscores=True
        )
        source_ttl_ms = await self.redis.pttl(source_key)
        migrated_members = 0
        unmapped_members = 0

        for batch in self._iter_batches(source_items):
            parsed_scores: dict[int, float] = {}
            for member, score in batch:
                try:
                    parsed_scores[int(member)] = float(score)
                except (TypeError, ValueError):
                    unmapped_members += 1

            if not parsed_scores:
                await self._refresh_lock(lock_token)
                continue

            channel_by_thread = await self._get_channel_by_thread_ids(
                list(parsed_scores)
            )

            channel_members: dict[int, dict[str, float]] = defaultdict(dict)
            mapped_ids = set(channel_by_thread)
            for thread_id, channel_id in channel_by_thread.items():
                channel_members[channel_id][str(thread_id)] = (
                    parsed_scores[thread_id]
                )

            async with self.redis.pipeline(transaction=True) as pipeline:
                for channel_id, members in channel_members.items():
                    destination_key = self.trend_service._get_channel_daily_key(
                        metric, day, channel_id
                    )
                    # 原子比较后写入，兼容不支持 ZADD GT 的旧 Redis 版本。
                    script_args = [
                        value
                        for member, score in members.items()
                        for value in (score, member)
                    ]
                    pipeline.eval(
                        self.ZADD_IF_GREATER_SCRIPT,
                        1,
                        destination_key,
                        *script_args,
                    )
                    if source_ttl_ms > 0:
                        pipeline.pexpire(destination_key, source_ttl_ms)
                await pipeline.execute()

            migrated_members += len(mapped_ids)
            unmapped_members += len(set(parsed_scores) - mapped_ids)
            await self._refresh_lock(lock_token)

        result = {
            "source_members": len(source_items),
            "migrated_members": migrated_members,
            "unmapped_members": unmapped_members,
        }
        if migrated_members + unmapped_members != len(source_items):
            raise RuntimeError(f"日榜 {source_key} 回填数量校验失败")

        await self.redis.hset(
            self.PROGRESS_KEY,
            source_key,
            json.dumps(result, ensure_ascii=False),
        )
        return result

    async def _get_channel_by_thread_ids(
        self, thread_ids: list[int]
    ) -> dict[int, int]:
        """批量从 PostgreSQL 获取帖子到频道的映射。"""
        async with self.session_factory() as session:
            statement = select(Thread.thread_id, Thread.channel_id).where(
                Thread.thread_id.in_(thread_ids)  # type: ignore
            )
            rows = (await session.execute(statement)).all()
        return {
            int(thread_id): int(channel_id) for thread_id, channel_id in rows
        }

    def _iter_batches(
        self, items: list[tuple[str, float]]
    ) -> Iterable[list[tuple[str, float]]]:
        """将 Redis 日榜成员按数据库查询批次切分。"""
        for start in range(0, len(items), self.batch_size):
            yield items[start : start + self.batch_size]

    async def _clear_channel_aggregate_caches(self) -> None:
        """删除频道聚合缓存，不触碰全局缓存和原始日榜。"""
        keys = [
            key
            async for key in self.redis.scan_iter(
                match="cache:surge:*:channel*", count=200
            )
        ]
        if keys:
            await self.redis.delete(*keys)

    async def _refresh_lock(self, lock_token: str) -> None:
        """仅在仍持有迁移锁时延长锁有效期。"""
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('expire', KEYS[1], ARGV[2])
        end
        return 0
        """
        refreshed = await self.redis.eval(
            script,
            1,
            self.LOCK_KEY,
            lock_token,
            self.LOCK_TTL_SECONDS,
        )
        if not refreshed:
            raise RuntimeError("频道趋势回填锁已失效")

    async def _release_lock(self, lock_token: str) -> None:
        """仅由锁持有者释放迁移锁。"""
        script = """
        if redis.call('get', KEYS[1]) == ARGV[1] then
            return redis.call('del', KEYS[1])
        end
        return 0
        """
        await self.redis.eval(script, 1, self.LOCK_KEY, lock_token)
