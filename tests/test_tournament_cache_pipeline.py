import json
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from core.api_cache_service import ApiCacheService
from core.cache_service import CacheService
from shared.enum.cache_keys import CacheKeys


@pytest.fixture(params=[ApiCacheService, CacheService], ids=["api", "bot"])
def tournament_cache(request, monkeypatch):
    """为两套真实缓存实现提供独立数据库与 Redis pipeline 替身。"""
    redis = MagicMock()
    redis.mget = AsyncMock()
    redis.setex = AsyncMock()
    pipelines = [MagicMock() for _ in range(3)]
    for pipeline in pipelines:
        pipeline.__aenter__ = AsyncMock(return_value=pipeline)
        pipeline.__aexit__ = AsyncMock(return_value=False)
        pipeline.execute = AsyncMock()
    redis.pipeline.side_effect = pipelines
    service = (
        request.param(None, redis)
        if request.param is ApiCacheService
        else request.param(None, None)
    )
    service._redis = redis
    repo = MagicMock()
    repo.get_tournament_info_by_thread_ids = AsyncMock()
    monkeypatch.setattr(
        f"{request.param.__module__}.BooklistItemRepository",
        MagicMock(return_value=repo),
    )
    return service, redis, pipelines, repo


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [100, 205])
async def test_cold_tournament_cache_uses_bounded_non_transactional_batches(
    tournament_cache, count
):
    """一百个冷项只执行一次回填，超限输入分批且保留每项 TTL。"""
    service, redis, pipelines, repo = tournament_cache
    thread_ids = list(range(1, count + 1))
    db_map = {tid: [{"booklist_id": 7, "booklist_name": "赛事"}] for tid in thread_ids}
    redis.mget.return_value = [None] * count
    repo.get_tournament_info_by_thread_ids.return_value = db_map
    assert await service.get_tournament_info_batch(None, thread_ids) == db_map
    repo.get_tournament_info_by_thread_ids.assert_awaited_once_with(thread_ids)
    batch_count = (count + 99) // 100
    assert redis.pipeline.call_args_list == [call(transaction=False)] * batch_count
    assert [pipe.setex.call_count for pipe in pipelines] == (
        [100, 0, 0] if count == 100 else [100, 100, 5]
    )
    for index, pipeline in enumerate(pipelines[:batch_count]):
        pipeline.execute.assert_awaited_once()
        batch = thread_ids[index * 100 : (index + 1) * 100]
        assert pipeline.setex.call_args_list == [
            call(
                CacheKeys.TOURNAMENT_THREAD.format(thread_id=tid),
                3600,
                json.dumps(db_map[tid]),
            )
            for tid in batch
        ]
    redis.setex.assert_not_awaited()


@pytest.mark.asyncio
async def test_mixed_hits_and_misses_cache_empty_results(tournament_cache):
    """命中项不回填，缺失且无赛事的帖子仍写入一小时空值缓存。"""
    service, redis, pipelines, repo = tournament_cache
    cached = [{"booklist_id": 1, "booklist_name": "已缓存"}]
    fresh = [{"booklist_id": 2, "booklist_name": "新赛事"}]
    redis.mget.return_value = [json.dumps(cached), "[]", None, None]
    repo.get_tournament_info_by_thread_ids.return_value = {3: fresh}
    result = await service.get_tournament_info_batch(None, [1, 2, 3, 4])
    assert result == {1: cached, 2: [], 3: fresh}
    repo.get_tournament_info_by_thread_ids.assert_awaited_once_with([3, 4])
    assert pipelines[0].setex.call_args_list == [
        call(CacheKeys.TOURNAMENT_THREAD.format(thread_id=3), 3600, json.dumps(fresh)),
        call(CacheKeys.TOURNAMENT_THREAD.format(thread_id=4), 3600, "[]"),
    ]
    pipelines[0].execute.assert_awaited_once()
    redis.pipeline.assert_called_once_with(transaction=False)


@pytest.mark.asyncio
async def test_pipeline_failure_returns_database_result_and_continues_batches(
    tournament_cache,
):
    """单批回填失败不影响数据库结果，也不阻止后续批次尝试。"""
    service, redis, pipelines, repo = tournament_cache
    thread_ids = list(range(101))
    db_map = {1: [{"booklist_id": 7, "booklist_name": "赛事"}]}
    redis.mget.return_value = [None] * len(thread_ids)
    repo.get_tournament_info_by_thread_ids.return_value = db_map
    pipelines[0].execute.side_effect = RuntimeError("redis unavailable")
    result = await service.get_tournament_info_batch(None, thread_ids)
    assert result == db_map
    assert redis.pipeline.call_count == 2
    pipelines[0].execute.assert_awaited_once()
    pipelines[1].execute.assert_awaited_once()
    assert pipelines[1].setex.call_count == 1


@pytest.mark.asyncio
async def test_all_hits_skip_database_and_pipeline(tournament_cache):
    """全命中时不增加数据库访问或空 pipeline 往返。"""
    service, redis, _, repo = tournament_cache
    cached = [{"booklist_id": 1, "booklist_name": "已有赛事"}]
    redis.mget.return_value = [json.dumps(cached), "[]"]
    result = await service.get_tournament_info_batch(None, [1, 2])
    assert result == {1: cached, 2: []}
    repo.get_tournament_info_by_thread_ids.assert_not_awaited()
    redis.pipeline.assert_not_called()
    redis.mget.assert_awaited_once()
    redis.setex.assert_not_awaited()
