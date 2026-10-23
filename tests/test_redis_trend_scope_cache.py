from unittest.mock import AsyncMock, call, patch

import pytest

from core.redis_trend_service import RedisTrendService


@pytest.mark.asyncio
@pytest.mark.parametrize("child_state", ["missing", "locked"])
async def test_scope_hit_skips_children_and_preserves_normalized_scope(child_state):
    """范围命中时忽略缺失或持锁的子缓存，并保持排序去重与分页行为。"""
    service = RedisTrendService()
    scope = service._get_channel_scope_cache_key("reaction", 7, [1, 2])
    redis = AsyncMock()
    redis.exists.return_value = True
    redis.zrevrange.return_value = ["101", service.EMPTY_MEMBER, "102"]
    # 子构建器若被触发，立即暴露热缓存仍依赖来源状态的回归。
    child = AsyncMock(side_effect=AssertionError(f"unexpected child: {child_state}"))
    with (
        patch("core.redis_trend_service.RedisManager.get_client", return_value=redis),
        patch.object(service, "_get_or_build_channel_cache", child),
    ):
        result = await service.get_top_surging_ids(
            "reaction", 7, limit=3, offset=2, channel_ids=[2, 1, 2]
        )
    assert result == [101, 102]
    redis.exists.assert_awaited_once_with(scope)
    redis.zrevrange.assert_awaited_once_with(scope, 2, 4)
    redis.set.assert_not_awaited()
    child.assert_not_awaited()
    assert scope != service._get_channel_scope_cache_key("collection", 7, [1, 2])
    assert scope != service._get_channel_scope_cache_key("reaction", 3, [1, 2])


@pytest.mark.asyncio
async def test_cold_scope_builds_each_unique_child_then_max_union():
    """冷范围仍逐频道跨天求和，再以 MAX 合并去重后的来源。"""
    service = RedisTrendService()
    redis = AsyncMock()
    redis.exists.return_value = False
    redis.set.return_value = True
    redis.zcard.return_value = 1
    redis.zrevrange.return_value = ["101"]
    scope = service._get_channel_scope_cache_key("reaction", 3, [1, 2])
    children = [service._get_channel_cache_key("reaction", 3, i) for i in (1, 2)]
    with patch("core.redis_trend_service.RedisManager.get_client", return_value=redis):
        assert await service.get_top_surging_ids(
            "reaction", 3, limit=10, channel_ids=[2, 1, 2]
        ) == [101]
    unions = redis.zunionstore.await_args_list
    assert len(unions) == 3
    for child_call, child_key in zip(unions[:2], children):
        assert child_call.args[0] == child_key
        assert len(child_call.args[1]) == 3
        assert child_call.kwargs == {"aggregate": "SUM"}
    assert unions[-1] == call(scope, children, aggregate="MAX")
    assert redis.exists.await_args_list.count(call(scope)) == 2
    assert redis.expire.await_count == 3
    assert redis.eval.await_count == 3


@pytest.mark.asyncio
async def test_scope_populated_during_child_build_is_rechecked():
    """来源构建期间其他请求填好范围时，保留聚合器的二次命中检查。"""
    service = RedisTrendService()
    redis = AsyncMock()
    redis.exists.side_effect = [False, True]
    scope = service._get_channel_scope_cache_key("reaction", 7, [1, 2])
    child = AsyncMock(side_effect=["child:1", "child:2"])
    with (
        patch("core.redis_trend_service.RedisManager.get_client", return_value=redis),
        patch.object(service, "_get_or_build_channel_cache", child),
    ):
        result = await service._get_or_build_channel_scope_cache("reaction", 7, [1, 2])
    assert result == scope
    assert redis.exists.await_args_list == [call(scope), call(scope)]
    assert child.await_count == 2
    redis.set.assert_not_awaited()
    redis.zunionstore.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, RuntimeError("redis unavailable")])
async def test_cold_scope_preserves_child_failure_behavior(failure):
    """冷范围的子缓存超时或异常仍阻止返回不完整排名。"""
    service = RedisTrendService()
    redis = AsyncMock()
    redis.exists.return_value = False
    child = AsyncMock(side_effect=["child:1", failure])
    aggregate = AsyncMock()
    with (
        patch("core.redis_trend_service.RedisManager.get_client", return_value=redis),
        patch.object(service, "_get_or_build_channel_cache", child),
        patch.object(service, "_get_or_build_aggregate_cache", aggregate),
    ):
        if isinstance(failure, Exception):
            with pytest.raises(RuntimeError, match="redis unavailable"):
                await service.get_top_surging_ids("reaction", 7, 10, channel_ids=[1, 2])
        else:
            assert (
                await service.get_top_surging_ids("reaction", 7, 10, channel_ids=[1, 2])
                == []
            )
    assert child.await_count == 2
    aggregate.assert_not_awaited()
    redis.zrevrange.assert_not_awaited()
