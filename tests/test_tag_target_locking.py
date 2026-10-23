from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from dto.events.tag_command import TagCommand
from models import Booklist, Thread
from shared.tag_error import TagError
from tag.custom_tag_service import CustomTagService


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["read", "proposals", "audit"])
@pytest.mark.parametrize("kind", ["thread", "booklist"])
async def test_target_reads_authorize_before_locks_and_never_lock_row(action, kind):
    """真实只读入口先核验权限，所有目标查询均不使用排他行锁。"""
    target = SimpleNamespace(
        id=1,
        thread_id=10,
        guild_id=20,
        channel_id=30,
        author_id=40,
        show_flag=True,
        not_found_count=0,
        owner_id=40,
        is_public=True,
    )
    model = Thread if kind == "thread" else Booklist
    statements = []
    authorized = False
    session = MagicMock()

    async def execute(statement):
        statements.append(statement)
        result = MagicMock()
        if str(statement).startswith("SELECT pg_advisory"):
            assert authorized
            return result
        assert statement._for_update_arg is None
        is_target = statement.column_descriptions[0].get("entity") is model
        result.scalars.return_value.all.return_value = [target] if is_target else []
        result.scalars.return_value.__iter__.return_value = iter([])
        return result

    async def authorize(*args, **kwargs):
        nonlocal authorized
        assert len(statements) == 1
        authorized = True
        return True

    session.execute = AsyncMock(side_effect=execute)
    access = SimpleNamespace(access=AsyncMock(side_effect=authorize))
    service = CustomTagService(session, {}, access=access)
    service.snapshot = AsyncMock(return_value={"tags": []})
    await service.dispatch(
        TagCommand(action, 40, {"target_type": kind, "target_id": 10})
    )
    assert authorized
    target_queries = [
        stmt
        for stmt in statements
        if not str(stmt).startswith("SELECT pg_advisory")
        and stmt.column_descriptions[0].get("entity") is model
    ]
    assert len(target_queries) == 2
    assert target_queries[1].get_execution_options()["populate_existing"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changed_field",
    [None, "author_id", "channel_id", "guild_id", "show_flag", "not_found_count"],
)
async def test_target_write_refreshes_locked_state_after_authorization(changed_field):
    """写操作保留行锁并拒绝网络等待期间变化的授权依据。"""
    target = SimpleNamespace(
        id=1,
        thread_id=10,
        guild_id=20,
        channel_id=30,
        author_id=40,
        show_flag=True,
        not_found_count=0,
    )
    statements = []
    session = MagicMock()

    async def execute(statement):
        statements.append(statement)
        result = MagicMock()
        if len(statements) == 3:
            assert statement._for_update_arg is not None
            assert statement.get_execution_options()["populate_existing"]
            if changed_field is not None:
                setattr(
                    target,
                    changed_field,
                    not target.show_flag if changed_field == "show_flag" else 999,
                )
        result.scalars.return_value.all.return_value = [target]
        return result

    async def authorize(*args, **kwargs):
        assert len(statements) == 1
        assert statements[0]._for_update_arg is None
        return True

    session.execute = AsyncMock(side_effect=execute)
    access = SimpleNamespace(access=AsyncMock(side_effect=authorize))
    service = CustomTagService(session, {}, access=access)
    payload = {"target_type": "thread", "target_id": 10}
    if changed_field is None:
        assert (await service.target(40, payload, manage=True))[3] is True
    else:
        with pytest.raises(TagError) as error:
            await service.target(40, payload, manage=True)
        assert error.value.detail["code"] == "concurrent_conflict"
    assert len(statements) == 3
    access.access.assert_awaited_once()


@pytest.mark.asyncio
async def test_denied_target_does_not_acquire_locks():
    """本次实时权限拒绝后不进入任何加锁区间。"""
    target = SimpleNamespace(
        id=1,
        thread_id=10,
        guild_id=20,
        channel_id=30,
        author_id=40,
        show_flag=True,
        not_found_count=0,
    )
    result = MagicMock()
    result.scalars.return_value.all.return_value = [target]
    session = SimpleNamespace(execute=AsyncMock(return_value=result))
    access = SimpleNamespace(
        access=AsyncMock(side_effect=TagError("forbidden", "角色已撤销", 403))
    )
    service = CustomTagService(session, {}, access=access)
    with pytest.raises(TagError) as error:
        await service.dispatch(
            TagCommand("read", 40, {"target_type": "thread", "target_id": 10})
        )
    assert error.value.status == 403
    assert session.execute.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner_id", "is_public", "deleted"])
@pytest.mark.parametrize("lock", [True, False])
async def test_booklist_target_rejects_changed_access_after_authorization(change, lock):
    """书单授权后改为私有、转移归属或删除时，中止读取及写入。"""
    target = SimpleNamespace(id=1, owner_id=40, is_public=True)
    statements = []
    session = MagicMock()

    async def execute(statement):
        statements.append(statement)
        result = MagicMock()
        rows = [target]
        if len(statements) == 3:
            assert (statement._for_update_arg is not None) == lock
            assert statement.get_execution_options()["populate_existing"]
            if change == "deleted":
                rows = []
            else:
                setattr(target, change, 99 if change == "owner_id" else False)
        result.scalars.return_value.all.return_value = rows
        return result

    session.execute = AsyncMock(side_effect=execute)
    access = SimpleNamespace(access=AsyncMock(return_value=True))
    service = CustomTagService(session, {}, access=access)
    with pytest.raises(TagError) as error:
        await service.target(40, {"target_type": "booklist", "target_id": 1}, lock=lock)
    expected_code = "not_found" if change == "deleted" else "concurrent_conflict"
    assert error.value.detail["code"] == expected_code
    access.access.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "payload", "exclusive"),
    [
        ("manage", {}, True),
        ("merge", {}, True),
        ("pool", {}, False),
        ("relations", {}, False),
        ("audit", {"target_type": "tag", "target_id": 1}, False),
    ],
)
async def test_non_target_dispatch_keeps_original_advisory_lock(
    action, payload, exclusive
):
    """池维护、池读取和标签审计仍在原有事务锁保护下执行。"""
    session = SimpleNamespace(execute=AsyncMock())
    service = CustomTagService(session, {})

    async def handler(actor, body):
        session.execute.assert_awaited_once()
        statement = str(session.execute.await_args.args[0])
        expected = (
            "pg_advisory_xact_lock" if exclusive else "pg_advisory_xact_lock_shared"
        )
        assert statement == f"SELECT {expected}(73902141)"
        assert actor == 40
        assert body == payload
        return "completed"

    setattr(service, action, handler)
    assert await service.dispatch(TagCommand(action, 40, payload)) == "completed"
