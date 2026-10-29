"""认证缓存的时间边界、错误分类和撤销竞态。"""

import hashlib
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Request
from identity.fluxer_auth_service import FluxerAuthService
from api.v1.routers import auth
from api.v1.dependencies import security


@pytest.fixture
def auth_service():
    """注入内存 Redis 与 NATS 协议替身，保留真实认证流程。"""
    values = {FluxerAuthService.PREFIX + ":generation": "1"}
    ttls = {}
    requests = []
    replies = [
        {
            "ok": True,
            "data": {
                "user_id": "200",
                "session_id_hash": "opaque_hash",
                "discord_user_id": "100",
                "username": "alice",
            },
        }
    ]
    hook = [None]

    async def get(key):
        return values.get(key)

    async def incr(key):
        values[key] = str(int(values.get(key, "0")) + 1)
        return int(values[key])

    async def evaluate(script, count, *args):
        keys, argv = args[:count], args[count:]
        if count == 2:
            if values.get(keys[0], "0") != argv[0]:
                return None if "return nil" in script else 0
            if len(argv) == 1:
                return values.get(keys[1])
            values[keys[1]] = argv[1]
            ttls[keys[1]] = argv[2]
            return 1
        raw = values.get(keys[0])
        if "cjson.decode" in script:
            if not raw or json.loads(raw)["binding"] != argv[0]:
                return None
            return values.pop(keys[0])
        if raw != argv[0]:
            return 0
        values.pop(keys[0])
        return 1

    async def request(subject, payload, timeout):
        requests.append((subject, json.loads(payload)))
        if hook[0]:
            await hook[0]()
        response = replies[0]
        if isinstance(response, Exception):
            raise response
        return SimpleNamespace(data=json.dumps(response).encode())

    service = FluxerAuthService(SimpleNamespace(get=get, incr=incr, eval=evaluate))
    service.connection = SimpleNamespace(request=request)
    service.ready = True
    return SimpleNamespace(
        service=service,
        values=values,
        ttls=ttls,
        requests=requests,
        replies=replies,
        hook=hook,
    )


@pytest.mark.asyncio
async def test_positive_cache_hashes_token_without_renewal(auth_service):
    """缓存命中不刷新 TTL，也不把 token 放进键名。"""
    token = "flx_very-secret-value"
    first = await auth_service.service.resolve(token)
    assert first["user_id"] == "200"
    initial = dict(auth_service.ttls)
    assert await auth_service.service.resolve(token) == first
    assert len(auth_service.requests) == 1
    assert auth_service.ttls == initial
    assert all(token not in key for key in auth_service.values)
    assert all(0 < ttl <= 3600 for ttl in auth_service.ttls.values())


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["INVALID_TOKEN", "ACCOUNT_UNAVAILABLE"])
async def test_only_definitive_invalid_tokens_receive_five_second_cache(
    auth_service, code
):
    """明确无效结果才短暂负缓存。"""
    auth_service.replies[0] = {"ok": False, "error": {"code": code, "retryable": False}}
    assert await auth_service.service.resolve("flx_invalid") is None
    assert await auth_service.service.resolve("flx_invalid") is None
    assert len(auth_service.requests) == 1
    assert all(0 < ttl <= 5 for ttl in auth_service.ttls.values())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        TimeoutError(),
        {"ok": False, "error": {"code": "UNAVAILABLE", "retryable": True}},
        {"bad": "response"},
    ],
)
async def test_dependency_failure_is_503_and_not_negative_cached(
    auth_service, response
):
    """超时、依赖错误和畸形响应不会伪装成用户凭证无效。"""
    auth_service.replies[0] = response
    with pytest.raises(HTTPException) as error:
        await auth_service.service.resolve("flx_valid")
    assert error.value.status_code == 503
    assert not auth_service.ttls


@pytest.mark.asyncio
async def test_inflight_resolve_cannot_repopulate_after_invalidation(auth_service):
    """RPC 飞行期间的撤销事件阻止旧结果写回与返回。"""
    auth_service.hook[0] = lambda: auth_service.service.invalidated(None)
    with pytest.raises(HTTPException) as error:
        await auth_service.service.resolve("flx_racing")
    assert error.value.status_code == 503
    assert not auth_service.ttls


@pytest.mark.asyncio
async def test_disconnect_refuses_cached_tokens_and_reconnect_flushes(auth_service):
    """断线不接受旧缓存，重连会强制重新查权威会话。"""
    await auth_service.service.resolve("flx_live")
    await auth_service.service.disconnected()
    with pytest.raises(HTTPException) as error:
        await auth_service.service.resolve("flx_live")
    assert error.value.status_code == 503
    await auth_service.service.reconnected()
    await auth_service.service.resolve("flx_live")
    assert len(auth_service.requests) == 2


@pytest.mark.asyncio
async def test_miss_concurrency_has_bounded_wait(auth_service):
    """所有外部请求槽占用时快速返回服务繁忙。"""
    for _ in range(64):
        await auth_service.service.misses.acquire()
    with pytest.raises(HTTPException) as error:
        await auth_service.service.resolve("flx_overload")
    assert error.value.status_code == 503
    assert not auth_service.requests


def request(path, headers=(), cookie=None):
    """构造真实 HTTP 请求作用域，Cookie 与 Bearer 明确区分。"""
    raw = [(name.lower().encode(), value.encode()) for name, value in headers]
    if cookie:
        raw.append((b"cookie", cookie.encode()))
    return Request({"type": "http", "method": "POST", "path": path, "headers": raw})


@pytest.mark.asyncio
async def test_fluxer_security_ignores_cookie_and_uses_authoritative_principal(
    monkeypatch,
):
    """Fluxer 不接受可被跨站自动附带的旧 session Cookie。"""
    monkeypatch.setenv("FORUM_AUTH_MODE", "fluxer")
    called = []

    async def resolve(token):
        called.append(token)
        return {"id": "200", "discord_user_id": None}

    monkeypatch.setattr(security, "resolve_principal", resolve)
    assert (
        await security.get_current_user(
            request("/v1/test", cookie="session=flx_cookie"), None
        )
        is None
    )
    principal = await security.get_current_user(
        request("/v1/test", [("Authorization", "Bearer flx_valid")]), None
    )
    assert principal["id"] == "200"
    assert principal["discord_user_id"] is None
    assert called == ["flx_valid"]


@pytest.mark.asyncio
async def test_claim_is_one_use_and_bound_to_browser_and_origin(
    monkeypatch, auth_service
):
    """泄露登录码不足以跨浏览器或跨来源领取会话。"""
    monkeypatch.setenv("FORUM_AUTH_MODE", "fluxer")
    monkeypatch.setattr(auth, "_AUTH_CONFIG", {"frontend_url": "https://forum.example"})
    monkeypatch.setattr(
        auth.RedisManager, "get_client", lambda: auth_service.service.redis
    )
    key = "forum:login:claim:" + hashlib.sha256(b"code").hexdigest()
    auth_service.values[key] = json.dumps(
        {
            "binding": hashlib.sha256(b"browser").hexdigest(),
            "token": "flx_secret",
            "user": {"id": "200"},
        }
    )
    for origin, cookie in [
        ("https://evil.example", "browser"),
        ("https://forum.example", "other-browser"),
    ]:
        with pytest.raises(HTTPException):
            await auth.claim_session(
                request(
                    "/v1/auth/session/claim",
                    [("Origin", origin)],
                    "forum_login_binding=" + cookie,
                ),
                {"login_code": "code"},
            )
        assert key in auth_service.values
    valid = request(
        "/v1/auth/session/claim",
        [("Origin", "https://forum.example")],
        "forum_login_binding=browser",
    )
    response = await auth.claim_session(valid, {"login_code": "code"})
    assert json.loads(response.body)["token"] == "flx_secret"
    assert response.headers["cache-control"] == "no-store"
    with pytest.raises(HTTPException):
        await auth.claim_session(valid, {"login_code": "code"})


@pytest.mark.asyncio
async def test_callback_rejects_missing_or_cross_browser_state(
    monkeypatch, auth_service
):
    """Discord OAuth 回调在读取授权码前校验 state 与浏览器绑定。"""
    monkeypatch.setattr(auth, "_AUTH_CONFIG", {"frontend_url": "https://forum.example"})
    monkeypatch.setattr(
        auth.RedisManager, "get_client", lambda: auth_service.service.redis
    )
    state_key = "forum:oauth:state:" + hashlib.sha256(b"state").hexdigest()
    auth_service.values[state_key] = hashlib.sha256(b"browser").hexdigest()
    result = await auth.callback(
        "code",
        request("/v1/auth/callback", cookie="forum_login_binding=attacker"),
        "state",
    )
    assert "status=400" in result.headers["location"]
    assert state_key in auth_service.values
    result = await auth.callback("code", request("/v1/auth/callback"), None)
    assert "status=400" in result.headers["location"]


@pytest.mark.asyncio
async def test_positive_ttl_starts_before_authoritative_request(
    monkeypatch, auth_service
):
    """外部请求耗时计入缓存寿命，而不是从 RPC 完成后重新开始。"""
    from identity import fluxer_auth_service

    now = [1000.0]
    monkeypatch.setattr(fluxer_auth_service.time, "time", lambda: now[0])

    async def slow_resolve():
        now[0] += 120.0

    auth_service.hook[0] = slow_resolve
    await auth_service.service.resolve("flx_slow_valid")
    assert list(auth_service.ttls.values()) == [3480]
