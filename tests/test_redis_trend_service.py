"""Redis 趋势频道日榜、范围聚合和历史回填测试。"""

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from core.redis_trend_channel_migrator import RedisTrendChannelMigrator
from core.redis_trend_service import RedisTrendService


async def _delete_metric_keys(redis_client, metric: str) -> None:
    """删除单个测试指标创建的全部 Redis 键。"""
    keys = [
        key
        async for key in redis_client.scan_iter(match=f"*{metric}*", count=100)
    ]
    if keys:
        await redis_client.delete(*keys)


@pytest.mark.asyncio
async def test_record_increment_writes_global_and_channel_daily_keys(redis_client):
    """单次趋势增量同时写入全局榜和频道榜并设置 TTL。"""
    metric = f"test_dual_write_{uuid4().hex}"
    service = RedisTrendService()
    now = datetime.now(timezone.utc)

    try:
        await service.record_increment(metric, 101, 2001, count=3)
        global_key = service._get_daily_key(metric, now)
        channel_key = service._get_channel_daily_key(metric, now, 2001)

        assert await redis_client.zscore(global_key, "101") == 3
        assert await redis_client.zscore(channel_key, "101") == 3
        assert await redis_client.ttl(global_key) > 0
        assert await redis_client.ttl(channel_key) > 0
    finally:
        await _delete_metric_keys(redis_client, metric)


@pytest.mark.asyncio
async def test_channel_scope_uses_max_and_handles_empty_channels(redis_client):
    """多频道子榜保持趋势顺序，并以 MAX 防止异常重复成员翻倍。"""
    metric = f"test_scope_{uuid4().hex}"
    service = RedisTrendService()
    now = datetime.now(timezone.utc)
    marker_key = service.CHANNEL_MIGRATION_COMPLETE_KEY
    previous_marker = await redis_client.get(marker_key)

    try:
        await redis_client.set(marker_key, "test-ready")
        await redis_client.zadd(
            service._get_channel_daily_key(metric, now, 1),
            {"101": 5, "103": 2},
        )
        await redis_client.zadd(
            service._get_channel_daily_key(metric, now, 2),
            {"102": 4, "103": 7},
        )

        result = await service.get_top_surging_ids(
            metric, days=1, limit=10, channel_ids=[2, 1, 2]
        )
        empty_result = await service.get_top_surging_ids(
            metric, days=1, limit=10, channel_ids=[999]
        )

        assert result == [103, 101, 102]
        assert empty_result == []
    finally:
        await _delete_metric_keys(redis_client, metric)
        if previous_marker is None:
            await redis_client.delete(marker_key)
        else:
            await redis_client.set(marker_key, previous_marker)


@pytest.mark.asyncio
async def test_channel_migration_is_idempotent_and_tracks_unmapped_members(
    redis_client, monkeypatch
):
    """历史回填可重复执行，并保留并发双写形成的更高频道分数。"""
    metric = f"test_migration_{uuid4().hex}"
    service = RedisTrendService()
    now = datetime.now(timezone.utc)
    source_key = service._get_daily_key(metric, now)
    channel_one_key = service._get_channel_daily_key(metric, now, 10)
    channel_two_key = service._get_channel_daily_key(metric, now, 20)

    try:
        await redis_client.zadd(source_key, {"1001": 5, "1002": 3, "9999": 2})
        await redis_client.expire(source_key, 3600)
        migrator = RedisTrendChannelMigrator(
            session_factory=None,  # type: ignore[arg-type]
            redis_client=redis_client,
            metrics=(metric,),
            max_days=1,
            batch_size=2,
        )

        async def get_channel_by_thread_ids(thread_ids):
            """模拟批量数据库频道映射，并保留一个已删除帖子。"""
            mapping = {1001: 10, 1002: 20}
            return {
                thread_id: mapping[thread_id]
                for thread_id in thread_ids
                if thread_id in mapping
            }

        monkeypatch.setattr(
            migrator,
            "_get_channel_by_thread_ids",
            get_channel_by_thread_ids,
        )

        result = await migrator.run(force=True, now=now)
        assert result["source_members"] == 3
        assert result["migrated_members"] == 2
        assert result["unmapped_members"] == 1
        assert await redis_client.zscore(channel_one_key, "1001") == 5
        assert await redis_client.zscore(channel_two_key, "1002") == 3

        await redis_client.zadd(channel_one_key, {"1001": 8})
        await migrator.run(force=True, now=now)
        assert await redis_client.zscore(channel_one_key, "1001") == 8
    finally:
        await _delete_metric_keys(redis_client, metric)
        await redis_client.delete(
            RedisTrendChannelMigrator.PROGRESS_KEY,
            RedisTrendChannelMigrator.LOCK_KEY,
            RedisTrendService.CHANNEL_MIGRATION_COMPLETE_KEY,
        )
