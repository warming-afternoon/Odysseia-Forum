import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

from api.v1.dependencies.security import get_current_user
from api.v1.routers import auth, meta
from author.cog import AuthorCog
from core.tag_access_service import TagAccessService
from core.user_role_service import UserRoleService
from shared.redis_client import RedisManager
from shared.tag_error import TagError


@pytest_asyncio.fixture
async def role_client(monkeypatch):
    """隔离配置和成员查询，验证真实 HTTP 路由而不访问 Discord。"""
    app = FastAPI()
    app.include_router(meta.router, prefix="/v1")
    # 登录 JWT 的旧身份组不能覆盖 Redis 快照或实时核验结果。
    app.dependency_overrides[get_current_user] = lambda: {"id": "123", "roles": ["77"]}
    config = {
        "main_guild_id": 10,
        "auth": {"guild_id": "10", "role_ids": "66"},
        "management_role_id": "77",
        "bot_admin_user_ids": [],
    }
    _mock_member_cache(monkeypatch, {})
    monkeypatch.setattr(meta, "role_config", config)
    roles = AsyncMock(return_value=set())

    async def get_member(self, user_id, guild_id):
        found_roles = await roles(user_id, guild_id)
        return {
            "roles": sorted(found_roles),
            "user": {"id": str(user_id), "username": "reader"},
        }

    monkeypatch.setattr(TagAccessService, "get_member", get_member)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        yield client, config, roles, app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "management,bot_admin", [(False, False), (True, False), (False, True), (True, True)]
)
async def test_role_flags_are_independent(role_client, management, bot_admin):
    """两种身份独立判断，包括仅 BOT 管理员但没有管理组身份。"""
    client, config, roles, _ = role_client
    config["bot_admin_user_ids"] = ["123"] if bot_admin else []
    roles.return_value = {"77"} if management else set()
    response = await client.get("/v1/meta/role")
    assert response.status_code == 200
    assert response.json() == {
        "is_management_member": management,
        "is_bot_admin": bot_admin,
    }
    assert response.headers["Cache-Control"] == "private, no-store"
    roles.assert_awaited_once_with(123, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("bot_admin", [False, True])
async def test_missing_member_preserves_bot_admin(role_client, bot_admin):
    """成员不存在只影响管理组身份，不影响 BOT 管理员配置。"""
    client, config, roles, _ = role_client
    config["bot_admin_user_ids"] = [123] if bot_admin else []
    roles.side_effect = TagError("forbidden", "用户不是服务器成员", 403)
    response = await client.get("/v1/meta/role")
    assert response.status_code == 200
    assert response.json() == {"is_management_member": False, "is_bot_admin": bot_admin}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        TagError("permission_unavailable", "成员权限服务暂时不可用", 503),
        httpx.ConnectError("连接失败"),
        ValueError("成员响应格式错误"),
    ],
)
async def test_lookup_failure_is_not_false_role(role_client, error):
    """临时失败返回 503，不伪造两个 false 的身份响应。"""
    client, _, roles, _ = role_client
    roles.side_effect = error
    response = await client.get("/v1/meta/role")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "permission_unavailable"
    assert response.headers["Cache-Control"] == "private, no-store"


@pytest.mark.asyncio
async def test_role_requires_login(role_client):
    """未登录返回 401，不能查询管理身份。"""
    client, _, roles, app = role_client
    app.dependency_overrides[get_current_user] = lambda: None
    response = await client.get("/v1/meta/role")
    assert response.status_code == 401
    roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_uninitialized_role_service(role_client, monkeypatch):
    """依赖尚未注入时明确返回 503。"""
    client, _, roles, _ = role_client
    monkeypatch.setattr(meta, "role_config", None)
    assert (await client.get("/v1/meta/role")).status_code == 503
    roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_management_configuration(role_client):
    """未配置管理组时无需成员查询，BOT 身份仍照常返回。"""
    client, config, roles, _ = role_client
    config.pop("management_role_id")
    config["bot_admin_user_ids"] = [123]
    response = await client.get("/v1/meta/role")
    assert response.json() == {"is_management_member": False, "is_bot_admin": True}
    roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_is_refreshed_between_requests(role_client):
    """缓存未命中时重复请求仍实时核验，不沿用 JWT 的旧身份组。"""
    client, _, roles, _ = role_client
    roles.side_effect = [{"77"}, set()]
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is True
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is False
    assert roles.await_count == 2


def _mock_member_cache(monkeypatch, values: dict, ttl: int = 86400):
    """用内存快照隔离 Redis，并支持真实成员事件删除缓存。"""

    async def setex(key, lifetime, value):
        values[key] = value

    redis = SimpleNamespace(
        setex=AsyncMock(side_effect=setex),
        get=AsyncMock(side_effect=lambda key: values.get(key)),
        ttl=AsyncMock(return_value=ttl),
        delete=AsyncMock(
            side_effect=lambda key: int(values.pop(key, None) is not None)
        ),
    )
    monkeypatch.setattr(RedisManager, "get_client", lambda: redis)
    return redis


@pytest.mark.asyncio
@pytest.mark.parametrize("cached_roles", [["77"], ["88"], []])
async def test_role_reuses_login_member_cache(role_client, monkeypatch, cached_roles):
    """复用登录流程写入的完整快照，缓存命中不查询 Discord 或续期。"""
    client, _, roles, _ = role_client
    member = {
        "roles": cached_roles,
        "user": {"id": "123", "username": "reader"},
        "roles_verified_at": time.time() - 12 * 60 * 60,
    }
    redis = _mock_member_cache(monkeypatch, {"user:discord:123": json.dumps(member)})

    assert await auth._get_cached_member("123") == member
    for _ in range(2):
        response = await client.get("/v1/meta/role")
        assert response.status_code == 200
        assert response.json() == {
            "is_management_member": "77" in cached_roles,
            "is_bot_admin": False,
        }
        assert response.headers["Cache-Control"] == "private, no-store"
    roles.assert_not_awaited()
    redis.delete.assert_not_awaited()
    redis.setex.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("event", ["roles_changed", "member_removed"])
async def test_member_event_invalidates_role_cache(role_client, monkeypatch, event):
    """真实成员事件删除登录缓存后，身份接口立即查询新的身份组。"""
    client, _, roles, _ = role_client
    values = {
        "user:discord:123": json.dumps(
            {"roles": ["77"], "roles_verified_at": time.time()}
        )
    }
    _mock_member_cache(monkeypatch, values)
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is True
    roles.assert_not_awaited()

    # 触发身份组变更或退服事件，验证共享成员缓存被删除。
    if event == "roles_changed":
        before = SimpleNamespace(id=123, roles=[77])
        after = SimpleNamespace(id=123, roles=[])
        await AuthorCog.on_member_update(None, before, after)
    else:
        await AuthorCog.on_member_remove(None, SimpleNamespace(id=123))
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is False
    roles.assert_awaited_once_with(123, 10)


@pytest.mark.asyncio
async def test_role_does_not_reuse_other_guild_cache(role_client, monkeypatch):
    """认证服务器和主服务器不同时，不用其他服务器的身份组判定。"""
    client, config, roles, _ = role_client
    config["auth"]["guild_id"] = "20"
    redis = _mock_member_cache(
        monkeypatch,
        {
            "user:discord:123": json.dumps(
                {"roles": ["77"], "roles_verified_at": time.time()}
            )
        },
    )
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is False
    roles.assert_awaited_once_with(123, 10)
    redis.get.assert_not_awaited()
    redis.setex.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_cache_is_isolated_by_user(role_client, monkeypatch):
    """不能将其他用户的管理身份复用到当前用户。"""
    client, _, roles, app = role_client
    app.dependency_overrides[get_current_user] = lambda: {"id": "124"}
    _mock_member_cache(
        monkeypatch,
        {
            "user:discord:123": json.dumps(
                {"roles": ["77"], "roles_verified_at": time.time()}
            )
        },
    )
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is False
    roles.assert_awaited_once_with(124, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "snapshot",
    [
        "invalid-json",
        "[]",
        '{"roles": "77", "roles_verified_at": 1}',
        '{"roles": [{}], "roles_verified_at": 1}',
        '{"roles": ["77"], "roles_verified_at": "invalid"}',
        json.dumps({"roles": ["77"], "roles_verified_at": time.time() - 90000}),
        json.dumps({"roles": ["77"], "roles_verified_at": time.time() + 90000}),
    ],
)
async def test_invalid_role_cache_falls_back(role_client, monkeypatch, snapshot):
    """损坏、过期或异常时间的缓存不能冒充有效管理身份。"""
    client, _, roles, _ = role_client
    _mock_member_cache(monkeypatch, {"user:discord:123": snapshot})
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is False
    roles.assert_awaited_once_with(123, 10)


@pytest.mark.asyncio
async def test_redis_failure_falls_back_to_discord(role_client, monkeypatch):
    """Redis 故障时仍能通过 Discord 查询身份。"""
    client, _, roles, _ = role_client
    redis = _mock_member_cache(monkeypatch, {})
    redis.get.side_effect = RuntimeError("Redis unavailable")
    roles.return_value = {"77"}
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is True
    roles.assert_awaited_once_with(123, 10)


@pytest.mark.asyncio
async def test_role_reuses_legacy_member_cache(role_client, monkeypatch):
    """历史缓存从剩余 TTL 推算验证时间后仍可复用。"""
    client, _, roles, _ = role_client
    redis = _mock_member_cache(
        monkeypatch, {"user:discord:123": '{"roles": ["77"]}'}, ttl=3600
    )
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is True
    roles.assert_not_awaited()
    redis.ttl.assert_awaited_once_with("user:discord:123")


@pytest.mark.asyncio
@pytest.mark.parametrize("management", [True, False])
async def test_role_cache_miss_refills_for_next_request(
    role_client, monkeypatch, management
):
    """缓存未命中后回填完整成员快照，下一次请求直接复用。"""
    client, _, roles, _ = role_client
    values = {}
    redis = _mock_member_cache(monkeypatch, values)
    roles.return_value = {"66", "77"} if management else {"66"}
    started = time.time()

    for _ in range(2):
        response = await client.get("/v1/meta/role")
        assert response.status_code == 200
        assert response.json()["is_management_member"] is management
    roles.assert_awaited_once_with(123, 10)
    redis.setex.assert_awaited_once()
    assert redis.setex.await_args.args[:2] == ("user:discord:123", 86400)
    cached = await auth._get_cached_member("123")
    assert set(cached["roles"]) == roles.return_value
    assert cached["user"] == {"id": "123", "username": "reader"}
    assert started <= cached["roles_verified_at"] <= time.time()


@pytest.mark.asyncio
async def test_role_change_rebuilds_then_reuses_cache(role_client, monkeypatch):
    """身份组变更删除缓存后重新查询并回填最新结果。"""
    client, _, roles, _ = role_client
    values = {}
    redis = _mock_member_cache(monkeypatch, values)
    roles.return_value = {"66", "77"}
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is True
    await AuthorCog.on_member_update(
        None,
        SimpleNamespace(id=123, roles=[66, 77]),
        SimpleNamespace(id=123, roles=[66]),
    )
    roles.return_value = {"66"}
    for _ in range(2):
        assert (await client.get("/v1/meta/role")).json()[
            "is_management_member"
        ] is False
    assert roles.await_count == 2
    assert redis.setex.await_count == 2
    assert json.loads(values["user:discord:123"])["roles"] == ["66"]


@pytest.mark.asyncio
async def test_role_without_forum_access_is_not_cached(role_client, monkeypatch):
    """失去论坛访问身份组的成员不能写入登录共享缓存。"""
    client, _, roles, _ = role_client
    redis = _mock_member_cache(monkeypatch, {})
    roles.return_value = {"77"}
    assert (await client.get("/v1/meta/role")).json()["is_management_member"] is True
    redis.setex.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_write_failure_preserves_role_response(role_client, monkeypatch):
    """Redis 写入失败仍返回本次 Discord 查询得到的真实身份。"""
    client, _, roles, _ = role_client
    redis = _mock_member_cache(monkeypatch, {})
    redis.setex.side_effect = RuntimeError("Redis write unavailable")
    roles.return_value = {"66", "77"}
    response = await client.get("/v1/meta/role")
    assert response.status_code == 200
    assert response.json()["is_management_member"] is True
    roles.assert_awaited_once_with(123, 10)
    redis.setex.assert_awaited_once()


@pytest.mark.asyncio
async def test_full_discord_response_is_cached(monkeypatch):
    """真实成员查询回填用户资料，敏感操作仍重新查询 Discord。"""
    values = {}
    _mock_member_cache(monkeypatch, values)
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    requests = []
    user = {"id": "123", "username": "reader", "avatar": "avatar-id"}

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"roles": ["66", "77"], "user": user})

    http_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: http_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    config = {
        "main_guild_id": 10,
        "auth": {"guild_id": "10", "role_ids": "66"},
        "management_role_id": "77",
    }
    assert (await UserRoleService(config).get_role(123)).is_management_member is True
    assert (await UserRoleService(config).get_role(123)).is_management_member is True
    assert len(requests) == 1
    assert (await auth._get_cached_member("123"))["user"] == user
    assert requests[0].url.path == "/api/v10/guilds/10/members/123"
    assert requests[0].headers["Authorization"] == "Bot test-token"

    # 验证权限检查独立查询 Discord，不使用身份展示的 Redis 缓存。
    assert await TagAccessService(config).roles(123, 10) == {"66", "77"}
    assert len(requests) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,payload",
    [
        (429, {}),
        (500, {}),
        (200, []),
        (200, {"roles": "77"}),
        (200, {"roles": [{}]}),
        (200, {"roles": ["66", "77"], "user": {"id": "999"}}),
    ],
)
async def test_failed_discord_response_does_not_write_cache(
    monkeypatch, status, payload
):
    """外部服务失败或成员响应异常时不回填，也不伪造普通成员。"""
    redis = _mock_member_cache(monkeypatch, {})
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    http_client = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status, json=payload)
    )
    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: http_client(transport=transport, **kwargs),
    )
    config = {
        "main_guild_id": 10,
        "auth": {"guild_id": "10", "role_ids": "66"},
        "management_role_id": "77",
    }
    with pytest.raises(TagError) as error:
        await UserRoleService(config).get_role(123)
    assert error.value.status == 503
    redis.setex.assert_not_awaited()
