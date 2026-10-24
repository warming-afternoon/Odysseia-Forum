from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.dialects import postgresql

from discovery.discovery_repository import DiscoveryRepository
from discovery.discovery_service import DiscoveryService
from dto.preferences.user_search_preferences_dto import UserSearchPreferencesDTO
from models import Tag
from shared import fts_utils


@pytest.fixture
def discovery_memory(monkeypatch):
    """使用真实发现服务、仓储和分词逻辑，仅替换数据库及趋势读取。"""
    memory = SimpleNamespace(tag_queries=[], thread_queries=[], surge_count=30)
    memory.tokenize = Mock(wraps=fts_utils.batch_cut)
    monkeypatch.setattr(fts_utils, "batch_cut", memory.tokenize)

    async def execute(statement):
        """记录真实查询并返回内存数据，避免连接 PostgreSQL 或 Redis。"""
        if statement.column_descriptions[0]["entity"] is Tag:
            memory.tag_queries.append(statement)
            return Mock(
                **{
                    "fetchall.return_value": [
                        (2, "include"),
                        (3, "exclude"),
                        (4, "new"),
                    ]
                }
            )
        memory.thread_queries.append(statement)
        params = statement.compile().params
        identifiers = params.get("thread_id_1", list(range(20)))
        if "thread_id_1" in params:
            identifiers = identifiers[: memory.surge_count]
        rows = [SimpleNamespace(thread_id=identifier) for identifier in identifiers]
        return Mock(
            **{"scalars.return_value.unique.return_value.all.return_value": rows}
        )

    async def trend_ids(metric, days, limit, offset, channel_ids=None):
        """按指标和偏移生成不重复候选，让真实服务完成补偿与截断。"""
        metric_bases = {
            "reaction": 100,
            "reply": 1000,
            "collection": 10000,
        }
        assert days == 30
        assert channel_ids == [11]
        base = metric_bases[metric] + offset
        return list(range(base, base + limit))

    memory.session = SimpleNamespace(execute=AsyncMock(side_effect=execute))
    memory.service = DiscoveryService(memory.session, exclude_channel_ids=[99])
    memory.service.trend_service = SimpleNamespace(
        get_top_surging_ids=AsyncMock(side_effect=trend_ids)
    )
    memory.repo = memory.service.repo
    memory.fts = AsyncMock(wraps=memory.repo.thread_repo.get_fts_matched_thread_ids)
    monkeypatch.setattr(
        memory.repo.thread_repo, "get_fts_matched_thread_ids", memory.fts
    )
    memory.prefs = UserSearchPreferencesDTO(
        user_id=1,
        preferred_channels=[11, 12],
        exclude_authors=[88],
        include_tags=["include"],
        exclude_tags=["exclude"],
        include_keywords="sunrise",
        exclude_keywords="rainfall",
    )
    return memory


@pytest.mark.asyncio
@pytest.mark.parametrize("compensate", [False, True])
async def test_four_rails_reuse_preference_conditions(discovery_memory, compensate):
    """四条轨道及其补偿只解析一次偏好且保留独立范围与排序。"""
    memory = discovery_memory
    memory.surge_count = 1 if compensate else 30
    rails = await memory.service.get_discovery_rails(10, 30, memory.prefs, [11])
    assert len(memory.tag_queries) == 1
    assert memory.fts.await_count == 1
    # 正选和反选各分词一次，使用真实 FTS 构建而非预制条件。
    assert memory.tokenize.call_count == 2
    assert len(memory.thread_queries) == (10 if compensate else 4)
    assert len(rails["latest"]) == 10
    assert len(rails["reaction_surge"]) == (3 if compensate else 10)
    for index, query in enumerate(memory.thread_queries):
        sql = str(
            query.compile(
                dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
            )
        )
        assert "thread.channel_id IN (11)" in sql
        assert "thread.channel_id IN (11, 12)" in sql
        assert "thread.channel_id NOT IN (99)" in sql
        assert "thread.author_id NOT IN (88)" in sql
        assert "thread.not_found_count = 0" in sql
        assert "thread.show_flag IS true" in sql
        assert "EXISTS" in sql and "tag.id IN (2)" in sql and "tag.id IN (3)" in sql
        assert "sunrise" in sql and "rainfall" in sql
        assert ("ORDER BY thread.created_at DESC" in sql) is (index == 0)
        assert ("ORDER BY CASE thread.thread_id" in sql) is (index != 0)


@pytest.mark.asyncio
async def test_single_rail_compensation_also_reuses_conditions(discovery_memory):
    """单轨三轮补偿复用偏好且每轮仍保留自己的候选 ID。"""
    memory = discovery_memory
    memory.surge_count = 1
    threads = await memory.service.get_single_rail(
        "reaction_surge", 10, 0, 30, memory.prefs, [11]
    )
    assert [thread.thread_id for thread in threads] == [100, 130, 160]
    assert len(memory.tag_queries) == 1
    assert memory.fts.await_count == 1
    assert memory.tokenize.call_count == 2
    assert len(memory.thread_queries) == 3
    ranges = [query.compile().params["thread_id_1"] for query in memory.thread_queries]
    assert [identifiers[0] for identifiers in ranges] == [100, 130, 160]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "change", ["user", "tags", "keywords", "markers", "authors", "channels"]
)
async def test_changed_preference_values_rebuild_conditions(discovery_memory, change):
    """同仓储内用户及偏好值变化重新解析，包括列表原地修改。"""
    memory = discovery_memory
    await memory.repo.get_latest_threads(10, 0, memory.prefs, [11])
    if change == "user":
        memory.prefs.user_id = 2
    elif change == "tags":
        memory.prefs.include_tags.append("new")
    elif change == "keywords":
        memory.prefs.include_keywords = "moonlight"
    elif change == "markers":
        memory.prefs.exclude_keyword_exemption_markers.append("never")
    elif change == "authors":
        memory.prefs.exclude_authors.append(77)
    else:
        memory.prefs.preferred_channels.append(13)
    await memory.repo.get_latest_threads(10, 0, memory.prefs, [11])
    assert len(memory.tag_queries) == 2
    assert memory.fts.await_count == 2
    assert memory.tokenize.call_count == 4
    # 相同值的另一 DTO 也应命中，而非依赖对象身份。
    await memory.repo.get_latest_threads(
        10, 0, memory.prefs.model_copy(deep=True), [11]
    )
    assert memory.fts.await_count == 2
    sql = str(
        memory.thread_queries[-1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    expected = {
        "tags": "tag.id IN (2, 4)",
        "keywords": "moonlight",
        "markers": "never",
        "authors": "thread.author_id NOT IN (88, 77)",
        "channels": "thread.channel_id IN (11, 12, 13)",
    }
    if change in expected:
        assert expected[change] in sql


@pytest.mark.asyncio
async def test_none_and_other_repository_do_not_reuse_user_conditions(discovery_memory):
    """空偏好不污染已有缓存，其他请求的仓储实例独立解析。"""
    memory = discovery_memory
    await memory.repo.get_latest_threads(10, 0, memory.prefs, [11])
    await memory.repo.get_latest_threads(5, 7, None, [22])
    sql = str(
        memory.thread_queries[-1].compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )
    assert "thread.channel_id IN (22)" in sql
    assert "LIMIT 5 OFFSET 7" in sql
    assert "sunrise" not in sql and "NOT IN (88)" not in sql
    assert "thread.channel_id IN (11, 12)" not in sql
    await memory.repo.get_latest_threads(10, 0, memory.prefs, [])
    assert memory.thread_queries[-1].compile().params["channel_id_1"] == []
    assert memory.fts.await_count == 1
    other_repo = DiscoveryRepository(memory.session)
    await other_repo.get_latest_threads(10, 0, memory.prefs, [11])
    assert len(memory.tag_queries) == 2
    assert memory.tokenize.call_count == 4


@pytest.mark.asyncio
async def test_failed_preparation_does_not_cache_partial_conditions(discovery_memory):
    """标签解析后全文构建失败时不缓存半成品，重试重新完成全部解析。"""
    memory = discovery_memory
    original_builder = memory.fts._mock_wraps
    memory.fts.side_effect = RuntimeError("tokenization failed")
    with pytest.raises(RuntimeError, match="tokenization failed"):
        await memory.repo.get_latest_threads(10, 0, memory.prefs, [11])
    assert memory.repo._preference_conditions == {}
    assert memory.thread_queries == []
    memory.fts.side_effect = original_builder
    await memory.repo.get_latest_threads(10, 0, memory.prefs, [11])
    await memory.repo.get_latest_threads(10, 0, memory.prefs, [11])
    assert len(memory.tag_queries) == 2
    assert memory.fts.await_count == 2
    assert memory.tokenize.call_count == 2
