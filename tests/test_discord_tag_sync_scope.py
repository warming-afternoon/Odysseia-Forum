from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.sql.selectable import Select

from core.discord_tag_source_repository import DiscordTagSourceRepository
from core.discord_tag_sync_service import DiscordTagSyncService
from dto.events.discord_tags_snapshot import DiscordTagsSnapshot
from models import DiscordTagSource, DiscordTagSyncState, Tag, TagBinding
from models.operation_log import OperationLog


@pytest.fixture
def sync_memory(monkeypatch):
    """以内存记录模拟仓储结果并录制真实服务生成的查询范围。"""
    now = datetime(2026, 10, 24, 12, tzinfo=UTC).replace(tzinfo=None)
    tags = [
        Tag(id=index, name=name, source="discord")
        for index, name in enumerate("ABC", 1)
    ]
    sources = [
        DiscordTagSource(
            id=index,
            discord_tag_id=index * 100,
            channel_id=20,
            name=tag.name,
            tag_id=tag.id,
        )
        for index, tag in enumerate(tags[:2], 1)
    ]
    bindings = [
        TagBinding(
            id=index,
            target_type="thread",
            target_id=10,
            tag_id=index,
            binding_source="discord_sync",
            discord_source_id=index,
        )
        for index in (1, 2)
    ]
    memory = SimpleNamespace(
        sources=sources,
        tags=tags,
        bindings=bindings,
        added=[],
        scopes=[],
        updates=[],
        state=DiscordTagSyncState(channel_id=20, observed_at=now),
        now=now,
    )

    async def execute(statement):
        """按真实查询参数提供内存结果，记录绑定扫描及作品修订更新。"""
        if not isinstance(statement, Select):
            if getattr(statement, "is_update", False):
                memory.updates.append(statement.compile().params)
            return Mock()
        params = statement.compile().params
        description = statement.column_descriptions[0]
        entity = description["entity"]
        rows = []
        if entity is DiscordTagSource:
            rows = memory.sources
            if description["name"] == "tag_id":
                rows = [source.tag_id for source in rows if source.deleted_at is None]
        elif entity is TagBinding:
            rows = [binding for binding in memory.bindings if binding.ended_at is None]
            if "discord_source_id_1" in params:
                scope = set(params["discord_source_id_1"])
                memory.scopes.append(scope)
                rows = [
                    binding
                    for binding in rows
                    if binding.discord_source_id in scope
                    and binding.binding_source == "discord_sync"
                ]
            else:
                rows = [
                    binding
                    for binding in rows
                    if binding.target_id in params["target_id_1"]
                ]
        elif entity is Tag:
            rows = [tag for tag in tags if tag.source == params["source_1"]]
            if "id_1" in params:
                rows = [tag for tag in rows if tag.id in params["id_1"]]
            if "name_1" in params:
                rows = [tag for tag in rows if tag.name in params["name_1"]]
        return Mock(**{"scalars.return_value": rows})

    def add(record):
        """为新增记录赋内存标识并保留待写对象供行为断言。"""
        memory.added.append(record)
        if isinstance(record, DiscordTagSource):
            record.id = max([source.id for source in sources], default=0) + 1
            sources.append(record)
        elif isinstance(record, TagBinding):
            record.id = max([binding.id for binding in bindings], default=0) + 1
            bindings.append(record)
        elif isinstance(record, DiscordTagSyncState):
            memory.state = record

    async def flush():
        """在每个刷新点检查活跃来源及绑定的唯一性，覆盖名称交换顺序。"""
        source_keys = [
            (source.channel_id, source.tag_id)
            for source in sources
            if source.deleted_at is None
        ]
        binding_keys = [
            (binding.target_type, binding.target_id, binding.tag_id)
            for binding in bindings
            if binding.ended_at is None
        ]
        assert len(source_keys) == len(set(source_keys))
        assert len(binding_keys) == len(set(binding_keys))

    memory.session = SimpleNamespace(
        execute=AsyncMock(side_effect=execute),
        get=AsyncMock(return_value=memory.state),
        add=Mock(side_effect=add),
        flush=AsyncMock(side_effect=flush),
    )
    monkeypatch.setattr(
        DiscordTagSourceRepository,
        "concepts",
        AsyncMock(return_value={tag.name: tag for tag in tags}),
    )
    return memory


@pytest.mark.asyncio
@pytest.mark.parametrize("checkpoint_exists", [True, False])
async def test_unchanged_snapshot_skips_bindings_and_advances_checkpoint(
    sync_memory, checkpoint_exists
):
    """相同来源确认和首次检查点均不扫描绑定，也不报告公开池变化。"""
    memory = sync_memory
    if not checkpoint_exists:
        memory.session.get.return_value = None
    observed = memory.now + timedelta(seconds=1)
    changed = await DiscordTagSyncService(memory.session).apply(
        DiscordTagsSnapshot(20, {100: "A", 200: "B"}, observed)
    )
    assert changed is False
    assert memory.scopes == []
    assert memory.updates == []
    assert memory.state.observed_at == observed
    assert all(source.synced_at == observed for source in memory.sources)
    assert not any(isinstance(record, OperationLog) for record in memory.added)


@pytest.mark.asyncio
@pytest.mark.parametrize("first", [True, False])
async def test_new_source_has_no_historical_bindings_to_scan(sync_memory, first):
    """首次创建或追加来源不扫描旧绑定，仍报告标签池变化。"""
    memory = sync_memory
    if first:
        memory.sources.clear()
        memory.bindings.clear()
        memory.session.get.return_value = None
    snapshot = {300: "C"} if first else {100: "A", 200: "B", 300: "C"}
    observed = memory.now + timedelta(seconds=1)
    changed = await DiscordTagSyncService(memory.session).apply(
        DiscordTagsSnapshot(20, snapshot, observed)
    )
    assert changed is True
    assert memory.scopes == []
    assert memory.state.observed_at == observed
    assert any(source.discord_tag_id == 300 for source in memory.sources)


@pytest.mark.asyncio
@pytest.mark.parametrize("swap", [True, False])
async def test_rename_only_scans_changed_sources_and_preserves_swap(sync_memory, swap):
    """单来源改名只扫描自身，交换名称时先结束两侧旧轮次再新增。"""
    memory = sync_memory
    snapshot = {100: "B", 200: "A"} if swap else {100: "C", 200: "B"}
    old_bindings = list(memory.bindings)
    changed = await DiscordTagSyncService(memory.session).apply(
        DiscordTagsSnapshot(20, snapshot, memory.now + timedelta(seconds=1))
    )
    assert changed is True
    assert memory.scopes == [{1, 2} if swap else {1}]
    assert old_bindings[0].end_reason == "discord_rename"
    assert (old_bindings[1].ended_at is not None) is swap
    active = {
        binding.discord_source_id: binding.tag_id
        for binding in memory.bindings
        if binding.ended_at is None
    }
    assert active == ({1: 2, 2: 1} if swap else {1: 3, 2: 2})
    assert len(memory.updates) == 1


@pytest.mark.asyncio
async def test_delete_converts_binding_and_repeat_snapshot_skips_scan(sync_memory):
    """删除转换保留原绑定及审计，后续相同删除状态不重新扫描。"""
    memory = sync_memory
    service = DiscordTagSyncService(memory.session)
    changed = await service.apply(
        DiscordTagsSnapshot(20, {200: "B"}, memory.now + timedelta(seconds=1))
    )
    assert changed is True
    assert memory.scopes == [{1}]
    assert memory.bindings[0].binding_source == "local"
    assert memory.bindings[0].discord_source_id is None
    assert memory.bindings[0].ended_at is None
    assert any(
        isinstance(record, OperationLog) and record.type == "tag.binding.convert"
        for record in memory.added
    )
    memory.scopes.clear()
    assert (
        await service.apply(
            DiscordTagsSnapshot(20, {200: "B"}, memory.now + timedelta(seconds=2))
        )
        is False
    )
    assert memory.scopes == []
    assert memory.state.observed_at == memory.now + timedelta(seconds=2)


@pytest.mark.asyncio
async def test_restored_source_keeps_previously_converted_local_binding(sync_memory):
    """已删除来源恢复会纳入变化范围，但不会把本地历史绑定自动收回。"""
    memory = sync_memory
    memory.sources[0].deleted_at = memory.now - timedelta(seconds=1)
    memory.bindings[0].binding_source = "local"
    memory.bindings[0].discord_source_id = None
    changed = await DiscordTagSyncService(memory.session).apply(
        DiscordTagsSnapshot(20, {100: "A", 200: "B"}, memory.now + timedelta(seconds=1))
    )
    assert changed is True
    assert memory.scopes == [{1}]
    assert memory.sources[0].deleted_at is None
    assert memory.bindings[0].binding_source == "local"
    assert memory.updates == []


@pytest.mark.asyncio
async def test_stale_snapshot_returns_before_source_and_binding_queries(sync_memory):
    """旧快照不修改来源和检查点，也不执行来源或绑定查询。"""
    memory = sync_memory
    changed = await DiscordTagSyncService(memory.session).apply(
        DiscordTagsSnapshot(20, {}, memory.now - timedelta(seconds=1))
    )
    assert changed is False
    assert memory.session.execute.await_count == 1
    assert memory.session.flush.await_count == 0
    assert memory.state.observed_at == memory.now
    assert memory.sources[0].deleted_at is None
    assert memory.scopes == []


@pytest.mark.asyncio
async def test_empty_snapshot_converts_all_sources_once(sync_memory):
    """空快照仍删除全部有效来源，重复空快照只推进检查点。"""
    memory = sync_memory
    service = DiscordTagSyncService(memory.session)
    first = memory.now + timedelta(seconds=1)
    assert await service.apply(DiscordTagsSnapshot(20, {}, first)) is True
    assert memory.scopes == [{1, 2}]
    assert all(source.deleted_at == first for source in memory.sources)
    assert all(binding.binding_source == "local" for binding in memory.bindings)
    memory.scopes.clear()
    second = memory.now + timedelta(seconds=2)
    assert await service.apply(DiscordTagsSnapshot(20, {}, second)) is False
    assert memory.scopes == []
    assert memory.state.observed_at == second


@pytest.mark.asyncio
async def test_channel_backfill_changes_pool_without_migrating_bindings(sync_memory):
    """补齐历史来源频道会更新公开池，但不改变概念绑定归属。"""
    memory = sync_memory
    memory.sources[0].channel_id = None
    observed = memory.now + timedelta(seconds=1)
    changed = await DiscordTagSyncService(memory.session).apply(
        DiscordTagsSnapshot(20, {100: "A", 200: "B"}, observed)
    )
    assert changed is True
    assert memory.sources[0].channel_id == 20
    assert memory.scopes == []
    assert memory.updates == []
    assert memory.state.observed_at == observed
