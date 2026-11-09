"""Discord OAuth 与多 Bot Token 身份验证测试。"""

import asyncio
import hashlib
import json
import logging
import time
from collections import deque
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs, urlsplit

import fakeredis.aioredis
import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from starlette.requests import Request

from api.v1.routers import auth
from core.discord_member_verifier import DiscordMemberVerifier
from core.oauth_callback_cache import OAuthCallbackCache
from dto.discord_member_verification_dto import DiscordMemberVerificationDto


class QueueHttpClient:
    """按顺序返回预设 Discord 响应的异步客户端。"""

    def __init__(self, responses: list[httpx.Response | httpx.RequestError]):
        """保存预设响应并记录请求头。"""
        self.responses = deque(responses)
        self.authorization_headers: list[str] = []

    async def get(self, url: str, headers: dict) -> httpx.Response:
        """返回下一条响应。"""
        self.authorization_headers.append(headers["Authorization"])
        result = self.responses.popleft()
        if isinstance(result, httpx.RequestError):
            raise result
        return result


class FakeMemberVerifier:
    """为认证路由返回固定成员结果。"""

    def __init__(self, result: DiscordMemberVerificationDto, delay: float = 0.0):
        """保存固定结果。"""
        self.result = result
        self.delay = delay
        self.call_count = 0

    async def verify_member(self, user_id: str, attempt_id: str, client=None):
        """返回固定成员结果。"""
        self.call_count += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        return self.result


class QueueMemberVerifier:
    """按顺序返回成员验证结果，用于模拟身份组恢复。"""

    def __init__(self, results: list[DiscordMemberVerificationDto]):
        """保存等待依次返回的验证结果。"""
        self.results = deque(results)
        self.call_count = 0

    async def verify_member(self, user_id: str, attempt_id: str, client=None):
        """返回下一项成员验证结果。"""
        self.call_count += 1
        return self.results.popleft()


class FakeOAuthHttpClient:
    """模拟 OAuth token、用户信息两个 Discord 接口。"""

    token_request_count = 0

    async def __aenter__(self):
        """进入异步客户端上下文。"""
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        """退出异步客户端上下文。"""
        return None

    async def post(self, url: str, data: dict, headers: dict):
        """返回一次 OAuth token 成功响应。"""
        type(self).token_request_count += 1
        await asyncio.sleep(0.05)
        return httpx.Response(200, json={"access_token": "discord-access-token"})

    async def get(self, url: str, headers: dict):
        """返回 Discord 当前用户信息。"""
        return httpx.Response(
            200,
            json={"id": "123", "username": "reader", "global_name": "Reader"},
        )


class FakeAsyncSessionContext:
    """为 checkauth 未读查询提供空会话上下文。"""

    async def __aenter__(self):
        """返回占位会话。"""
        return object()

    async def __aexit__(self, exc_type, exc, traceback):
        """退出占位会话。"""
        return None


class FakeFollowRepository:
    """返回固定未读数量。"""

    def __init__(self, session):
        """接收占位数据库会话。"""
        self.session = session

    async def get_unread_count(self, user_id: int) -> int:
        """返回零条未读更新。"""
        return 0


def _response(status_code: int, payload: dict, headers: dict | None = None):
    """构造 Discord 测试响应。"""
    return httpx.Response(status_code, json=payload, headers=headers)


def _request_with_cookie() -> Request:
    """构造携带 session Cookie 的 Starlette 请求。"""
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/auth/checkauth",
            "headers": [(b"cookie", b"session=test-jwt")],
        }
    )


def _configure_auth(monkeypatch) -> None:
    """为路由测试注入最小认证配置。"""
    monkeypatch.setenv("FORUM_AUTH_MODE", "legacy")
    monkeypatch.setattr(
        auth,
        "_AUTH_CONFIG",
        {
            "client_id": "client-id",
            "client_secret": "client-secret",
            "redirect_uri": "https://api.example/v1/auth/callback",
            "guild_id": "guild-id",
            "role_ids": "allowed-role",
            "jwt_secret": "jwt-secret",
            "frontend_url": "https://forum.example",
            "bot_token": "primary-secret",
        },
    )


@pytest_asyncio.fixture
async def oauth_redis(monkeypatch):
    """通过支持 Lua 的 Redis 测试客户端运行完整事务逻辑。"""
    async with fakeredis.aioredis.FakeRedis(decode_responses=True) as redis:
        monkeypatch.setattr(auth.RedisManager, "get_client", lambda: redis)
        monkeypatch.setattr(auth, "_OAUTH_CALLBACK_CACHE", OAuthCallbackCache(redis))
        yield redis


async def _start_oauth(*, path: str = "/v1/auth/callback"):
    """用登录入口签发的 state 和 Cookie 构造回调请求。"""
    login_response = await auth.login(None)
    state = parse_qs(urlsplit(login_response.headers["location"]).query)["state"][0]
    cookie = SimpleCookie(login_response.headers["set-cookie"])["forum_login_binding"]
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "client": ("192.0.2.10", 12345),
            "headers": [
                (b"cookie", f"{cookie.key}={cookie.value}".encode()),
                (b"user-agent", b"first-browser"),
                (b"origin", b"https://forum.example"),
            ],
        }
    )
    return request, state


def _stub_unread_query(monkeypatch) -> None:
    """避免 checkauth 测试访问真实数据库。"""
    import core.follow_repository
    import shared.database

    monkeypatch.setattr(
        shared.database, "AsyncSessionFactory", lambda: FakeAsyncSessionContext()
    )
    monkeypatch.setattr(
        core.follow_repository, "ThreadFollowRepository", FakeFollowRepository
    )


@pytest.mark.asyncio
async def test_member_cache_refuses_missing_required_role(monkeypatch, oauth_redis):
    """缓存写入层拒绝保存缺少论坛身份组的成员快照。"""
    _configure_auth(monkeypatch)
    cache_key = "user:discord:123"
    await oauth_redis.set(cache_key, "stale-value")

    await auth._cache_member(
        "123",
        {
            "roles": ["other-role"],
            "user": {"id": "123", "username": "reader"},
            "roles_verified_at": time.time(),
        },
    )

    assert not await oauth_redis.exists(cache_key)


@pytest.mark.asyncio
async def test_token_pool_deduplicates_and_round_robins():
    """主 Token 与辅助 Token 去重后轮流作为首选。"""
    verifier = DiscordMemberVerifier(
        "guild", "primary-token", ["aux-token", "primary-token", ""]
    )
    client = QueueHttpClient(
        [
            _response(200, {"roles": []}),
            _response(200, {"roles": []}),
        ]
    )

    await verifier.verify_member("1", "attempt-1", client)
    await verifier.verify_member("2", "attempt-2", client)

    assert verifier.token_count == 2
    assert verifier.token_aliases == ["primary", "aux-1"]
    assert client.authorization_headers == ["Bot primary-token", "Bot aux-token"]


@pytest.mark.asyncio
async def test_token_pool_falls_back_after_rate_limit():
    """主 Token 被限流时使用辅助 Token 完成验证。"""
    verifier = DiscordMemberVerifier("guild", "primary-token", ["aux-token"])
    client = QueueHttpClient(
        [
            _response(
                429,
                {"message": "rate limited", "retry_after": 30.0},
                {"Retry-After": "30"},
            ),
            _response(200, {"roles": ["allowed-role"]}),
        ]
    )

    result = await verifier.verify_member("1", "attempt", client)

    assert result.outcome == "verified"
    assert result.token_alias == "aux-1"
    assert client.authorization_headers == ["Bot primary-token", "Bot aux-token"]


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403, 500])
async def test_token_pool_falls_back_after_recoverable_status(status_code):
    """凭证错误和 Discord 服务错误均会切换到辅助 Token。"""
    verifier = DiscordMemberVerifier("guild", "primary-token", ["aux-token"])
    client = QueueHttpClient(
        [
            _response(status_code, {"message": "temporary failure"}),
            _response(200, {"roles": ["allowed-role"]}),
        ]
    )

    result = await verifier.verify_member("1", "attempt", client)

    assert result.outcome == "verified"
    assert result.token_alias == "aux-1"


@pytest.mark.asyncio
async def test_token_pool_falls_back_after_timeout():
    """主 Token 网络超时时继续使用辅助 Token。"""
    verifier = DiscordMemberVerifier("guild", "primary-token", ["aux-token"])
    client = QueueHttpClient(
        [
            httpx.ReadTimeout("slow discord response"),
            _response(200, {"roles": ["allowed-role"]}),
        ]
    )

    result = await verifier.verify_member("1", "attempt", client)

    assert result.outcome == "verified"
    assert result.token_alias == "aux-1"


@pytest.mark.asyncio
async def test_token_pool_treats_404_as_definitive():
    """Discord 404 直接判定用户不是成员，不继续尝试其他 Token。"""
    verifier = DiscordMemberVerifier("guild", "primary-token", ["aux-token"])
    client = QueueHttpClient(
        [_response(404, {"code": 10007, "message": "Unknown Member"})]
    )

    result = await verifier.verify_member("1", "attempt", client)

    assert result.outcome == "not_member"
    assert client.authorization_headers == ["Bot primary-token"]


@pytest.mark.asyncio
async def test_token_pool_falls_back_when_bot_cannot_find_guild():
    """Bot 自身无法访问服务器时不能误判用户已经离开。"""
    verifier = DiscordMemberVerifier("guild", "primary-token", ["aux-token"])
    client = QueueHttpClient(
        [
            _response(404, {"code": 10004, "message": "Unknown Guild"}),
            _response(200, {"roles": ["allowed-role"]}),
        ]
    )

    result = await verifier.verify_member("1", "attempt", client)

    assert result.outcome == "verified"
    assert result.token_alias == "aux-1"


@pytest.mark.asyncio
async def test_duplicate_oauth_callback_exchanges_code_once(
    monkeypatch, caplog, oauth_redis
):
    """并发提交同一 code 时只调用一次 Discord token 接口。"""
    _configure_auth(monkeypatch)
    monkeypatch.setattr(
        auth,
        "_MEMBER_VERIFIER",
        FakeMemberVerifier(
            DiscordMemberVerificationDto(
                outcome="verified",
                member={
                    "roles": ["allowed-role"],
                    "user": {"id": "123", "username": "reader"},
                },
                status_code=200,
                token_alias="primary",
            ),
            delay=0.05,
        ),
    )

    async def no_cached_member(user_id: str):
        return None

    async def no_cache_write(user_id: str, member: dict):
        return None

    FakeOAuthHttpClient.token_request_count = 0
    monkeypatch.setattr(auth, "_get_cached_member", no_cached_member)
    monkeypatch.setattr(auth, "_cache_member", no_cache_write)
    monkeypatch.setattr(
        auth.httpx, "AsyncClient", lambda *args, **kwargs: FakeOAuthHttpClient()
    )
    caplog.set_level(logging.INFO)
    raw_code = "single-use-oauth-code"

    request, state = await _start_oauth()
    other_request, other_state = await _start_oauth()
    state_key = "forum:oauth:state:" + hashlib.sha256(state.encode()).hexdigest()
    await oauth_redis.pexpire(state_key, 15_000)
    responses = await asyncio.gather(
        auth.callback(raw_code, request, state),
        auth.callback(raw_code, request, state),
    )
    assert 0 < await oauth_redis.pttl(state_key) <= 15_000
    assert responses[0].headers["location"].startswith("https://forum.example#token=")
    assert responses[0].headers["location"] == responses[1].headers["location"]

    for code, browser, callback_state in (
        (raw_code, other_request, other_state),
        (raw_code, other_request, state),
        (raw_code, request, other_state),
        ("different-code", request, state),
    ):
        rejected = await auth.callback(code, browser, callback_state)
        assert "status=400" in rejected.headers["location"]

    rejected = await auth.callback(raw_code)
    assert "status=400" in rejected.headers["location"]

    await oauth_redis.pexpire(state_key, 1)
    await asyncio.sleep(0.01)
    rejected = await auth.callback(raw_code, request, state)
    assert "status=400" in rejected.headers["location"]

    assert FakeOAuthHttpClient.token_request_count == 1
    assert all(response.status_code == 302 for response in responses)
    assert raw_code not in caplog.text
    assert "discord-access-token" not in caplog.text
    assert "primary-secret" not in caplog.text


@pytest.mark.asyncio
async def test_oauth_role_failure_does_not_block_same_code_retry(
    monkeypatch, caplog, oauth_redis
):
    """缺少身份组不写长缓存，同一 code 可复用用户资料重新验证。"""
    _configure_auth(monkeypatch)
    verifier = QueueMemberVerifier(
        [
            DiscordMemberVerificationDto(
                outcome="verified",
                member={"roles": ["other-role"], "user": {"id": "123"}},
                status_code=200,
                token_alias="primary",
            ),
            DiscordMemberVerificationDto(
                outcome="verified",
                member={
                    "roles": ["allowed-role"],
                    "user": {"id": "123", "username": "reader"},
                },
                status_code=200,
                token_alias="aux-1",
            ),
        ]
    )
    cache_writes: list[dict] = []
    invalidations: list[tuple[str, str]] = []

    async def stale_unauthorized_member(user_id: str):
        return {
            "roles": ["other-role"],
            "user": {"id": user_id, "username": "reader"},
            "roles_verified_at": time.time(),
        }

    async def record_cache_write(user_id: str, member: dict):
        cache_writes.append(member)

    async def record_invalidation(user_id: str, reason: str):
        invalidations.append((user_id, reason))

    monkeypatch.setattr(auth, "_MEMBER_VERIFIER", verifier)
    monkeypatch.setattr(auth, "_get_cached_member", stale_unauthorized_member)
    monkeypatch.setattr(auth, "_cache_member", record_cache_write)
    monkeypatch.setattr(auth, "_delete_cached_member", record_invalidation)
    monkeypatch.setattr(
        auth.httpx, "AsyncClient", lambda *args, **kwargs: FakeOAuthHttpClient()
    )
    FakeOAuthHttpClient.token_request_count = 0
    caplog.set_level(logging.INFO)
    raw_code = "role-restored-code"

    request, state = await _start_oauth()
    failed_response = await auth.callback(raw_code, request, state)
    successful_response = await auth.callback(raw_code, request, state)

    assert failed_response.headers["location"].startswith(
        "https://forum.example/login?"
    )
    assert successful_response.headers["location"].startswith(
        "https://forum.example#token="
    )
    assert FakeOAuthHttpClient.token_request_count == 1
    assert verifier.call_count == 2
    assert len(cache_writes) == 1
    assert cache_writes[0]["roles"] == ["allowed-role"]
    assert ("123", "cached_missing_required_role") in invalidations
    assert ("123", "oauth_missing_required_role") in invalidations
    assert "OAuth登录成功" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("cached_age", [30, 180])
async def test_fluxer_callback_reuses_exchange_and_one_time_claim(
    monkeypatch, oauth_redis, cached_age
):
    """Fluxer 重试保留原始断言、请求来源与一次性交付结果。"""
    import shared.database

    _configure_auth(monkeypatch)
    monkeypatch.setenv("FORUM_AUTH_MODE", "fluxer")
    monkeypatch.setenv(
        "FORUM_V2_DISCORD_REDIRECT_URI", "https://api.example/v2/auth/callback"
    )
    cached_verified_at = time.time() - cached_age
    verified_member = {"roles": ["allowed-role"], "user": {"id": "123"}}
    monkeypatch.setattr(
        auth,
        "_get_cached_member",
        AsyncMock(
            return_value={**verified_member, "roles_verified_at": cached_verified_at}
        ),
    )
    write_member_cache = AsyncMock()
    monkeypatch.setattr(auth, "_cache_member", write_member_cache)
    verifier = FakeMemberVerifier(
        DiscordMemberVerificationDto(
            outcome="verified", member=verified_member, status_code=200
        )
    )
    monkeypatch.setattr(auth, "_MEMBER_VERIFIER", verifier)
    FakeOAuthHttpClient.token_request_count = 0
    monkeypatch.setattr(
        auth.httpx, "AsyncClient", lambda *args, **kwargs: FakeOAuthHttpClient()
    )

    rpc = AsyncMock(
        side_effect=[
            HTTPException(503, "模拟权威交换响应丢失"),
            {"token": "flx_test", "user_id": "200"},
            {"proof": "test_proof", "expires_in": 120},
        ]
    )
    monkeypatch.setattr(auth, "get_auth_service", lambda: SimpleNamespace(rpc=rpc))
    session = AsyncMock()
    session.execute.return_value = MagicMock(
        scalar_one_or_none=MagicMock(return_value=None)
    )
    session_context = MagicMock()
    session_context.__aenter__.return_value = session
    monkeypatch.setattr(shared.database, "AsyncSessionFactory", lambda: session_context)

    request, state = await _start_oauth(path="/v2/auth/callback")
    code = f"fluxer-code-{cached_age}"
    failed = await auth.callback(code, request, state)
    assert "status=503" in failed.headers["location"]

    # 模拟原浏览器在回包丢失后更换网络和请求头，再并发重试。
    retried = Request(
        {
            **request.scope,
            "client": ("192.0.2.20", 12345),
            "headers": [
                (key, b"retry-browser" if key == b"user-agent" else value)
                for key, value in request.scope["headers"]
            ],
        }
    )
    first, second = await asyncio.gather(
        auth.callback(code, retried, state), auth.callback(code, request, state)
    )
    assert first.headers["location"] == second.headers["location"]
    assert first.headers["cache-control"] == "no-store"
    assert FakeOAuthHttpClient.token_request_count == 1
    exchange, retry, prove = rpc.await_args_list
    assert exchange.args == retry.args == ("rpc.auth.v1.external.exchange",)
    assert exchange.kwargs == retry.kwargs
    if cached_age >= 120:
        assert verifier.call_count == 1
        verified_at = write_member_cache.await_args.args[1]["roles_verified_at"]
    else:
        assert verifier.call_count == 0
        verified_at = cached_verified_at
    assert (
        exchange.kwargs["verified_at"]
        == datetime.fromtimestamp(verified_at, timezone.utc).isoformat()
    )
    assert prove.kwargs["verified_at"] == exchange.kwargs["verified_at"]
    assert prove.kwargs["operation_id"] == exchange.kwargs["exchange_id"]
    assert exchange.kwargs["origin"]["user_agent"] == "first-browser"

    login_code = parse_qs(urlsplit(first.headers["location"]).fragment)["login_code"][0]
    claim_key = "forum:login:claim:" + hashlib.sha256(login_code.encode()).hexdigest()
    assert 0 < await oauth_redis.pttl(claim_key) <= 60_000
    other_request, _ = await _start_oauth(path="/v2/auth/callback")
    with pytest.raises(HTTPException, match="登录码失效或已使用"):
        await auth.claim_session(other_request, {"login_code": login_code})

    claim_response = await auth.claim_session(request, {"login_code": login_code})
    assert json.loads(claim_response.body)["token"] == "flx_test"
    with pytest.raises(HTTPException, match="登录码失效或已使用"):
        await auth.claim_session(request, {"login_code": login_code})
    consumed = await auth.callback(code, request, state)
    assert "status=400" in consumed.headers["location"]
    assert not await oauth_redis.exists(claim_key)
    assert rpc.await_count == 3


@pytest.mark.asyncio
async def test_oauth_expired_owner_can_only_resume_saved_progress(oauth_redis):
    """处理权过期只能恢复安全进度，原持有者不能迟到写入。"""
    cache = OAuthCallbackCache(oauth_redis)
    for code, expected in (("uncertain", "expired"), ("saved", "resume")):
        state_key = f"forum:oauth:state:{code}"
        await oauth_redis.set(state_key, "binding:legacy", ex=600)
        assert await cache.begin(state_key, "binding", "legacy", code, "first") == (
            "new",
            None,
        )

        progress = {"stage": "oauth_user", "user": {"id": "123"}}
        if expected == "resume":
            await cache.save(state_key, code, "first", progress)
        await oauth_redis.pexpire(f"forum:oauth:owner:{code}", 1)
        await asyncio.sleep(0.01)
        outcome, saved = await cache.begin(
            state_key, "binding", "legacy", code, "second"
        )
        assert outcome == expected
        assert saved == (progress if expected == "resume" else None)
        with pytest.raises(RuntimeError, match="登录事务已失效"):
            await cache.save(state_key, code, "first", progress)


@pytest.mark.asyncio
async def test_oauth_claim_expiry_does_not_reopen_delivery(oauth_redis):
    """登录码过期后仍保留事务已完成的状态。"""
    cache = OAuthCallbackCache(oauth_redis)
    state_key = "forum:oauth:state:short-claim"
    await oauth_redis.set(state_key, "binding:fluxer", px=5000)
    outcome, _ = await cache.begin(state_key, "binding", "fluxer", "code", "first")
    assert outcome == "new"
    delivery = {"login_code": "short-claim"}
    await cache.save(
        state_key,
        "code",
        "first",
        delivery,
        delivery=True,
        claim={"binding": "binding"},
    )
    claim_key = "forum:login:claim:" + hashlib.sha256(b"short-claim").hexdigest()
    assert (
        0 < await oauth_redis.pttl(claim_key) <= await oauth_redis.pttl(state_key) + 25
    )

    await oauth_redis.pexpire(claim_key, 1)
    await asyncio.sleep(0.01)
    assert await oauth_redis.exists(state_key)
    outcome, saved = await cache.begin(state_key, "binding", "fluxer", "code", "second")
    assert (outcome, saved) == ("expired", None)


@pytest.mark.asyncio
async def test_oauth_callback_rejects_legacy_cache_and_redis_failure(
    monkeypatch, oauth_redis
):
    """旧 code 缓存和 Redis 故障都不能跳过事务绑定。"""
    _configure_auth(monkeypatch)
    request, state = await _start_oauth()
    code = "legacy-cache-code"
    code_hash = hashlib.sha256(code.encode()).hexdigest()
    await oauth_redis.set(f"auth:oauth:state:{code_hash}", '{"stage":"authenticated"}')
    discord_client = MagicMock()
    monkeypatch.setattr(auth.httpx, "AsyncClient", discord_client)

    rejected = await auth.callback(code, request, state)
    assert "status=400" in rejected.headers["location"]

    monkeypatch.setattr(
        oauth_redis, "eval", AsyncMock(side_effect=ConnectionError("Redis 暂不可用"))
    )
    unavailable = await auth.callback(code, request, state)
    assert "status=503" in unavailable.headers["location"]
    discord_client.assert_not_called()


@pytest.mark.asyncio
async def test_oauth_failure_redirects_directly_to_login(monkeypatch):
    """OAuth 失败保持原有跳转流程，不由后端渲染错误页面。"""
    _configure_auth(monkeypatch)

    response = await auth.callback(None)

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://forum.example/login?")


@pytest.mark.asyncio
async def test_checkauth_revalidates_cached_missing_role(monkeypatch):
    """历史失败缓存视为未命中，本次 checkauth 立即实时复验。"""
    _configure_auth(monkeypatch)
    _stub_unread_query(monkeypatch)
    verifier = FakeMemberVerifier(
        DiscordMemberVerificationDto(
            outcome="verified",
            member={
                "roles": ["allowed-role"],
                "user": {"id": "123", "username": "reader"},
            },
            status_code=200,
            token_alias="aux-1",
        )
    )
    invalidations: list[tuple[str, str]] = []
    cache_writes: list[dict] = []

    async def verify_jwt(token: str, secret: str):
        return {
            "id": "123",
            "username": "reader",
            "roles": ["allowed-role"],
            "roles_verified_at": time.time(),
            "exp": time.time() + 3600,
        }

    async def cached_missing_role(user_id: str):
        return {
            "roles": ["other-role"],
            "user": {"id": user_id, "username": "reader"},
            "roles_verified_at": time.time(),
        }

    async def record_invalidation(user_id: str, reason: str):
        invalidations.append((user_id, reason))

    async def record_cache_write(user_id: str, member: dict):
        cache_writes.append(member)

    monkeypatch.setattr(auth, "verify_jwt", verify_jwt)
    monkeypatch.setattr(auth, "_get_cached_member", cached_missing_role)
    monkeypatch.setattr(auth, "_delete_cached_member", record_invalidation)
    monkeypatch.setattr(auth, "_cache_member", record_cache_write)
    monkeypatch.setattr(auth, "_MEMBER_VERIFIER", verifier)

    response = await auth.check_auth(_request_with_cookie())

    assert response.status_code == 200
    assert json.loads(response.body)["loggedIn"] is True
    assert verifier.call_count == 1
    assert invalidations == [("123", "cached_missing_required_role")]
    assert len(cache_writes) == 1
    assert cache_writes[0]["roles"] == ["allowed-role"]


@pytest.mark.asyncio
async def test_checkauth_keeps_fresh_roles_on_discord_failure(monkeypatch):
    """Discord 临时故障时继续信任 24 小时内验证过的 JWT 身份组。"""
    _configure_auth(monkeypatch)
    _stub_unread_query(monkeypatch)
    verified_at = time.time() - 60

    async def verify_jwt(token: str, secret: str):
        return {
            "id": "123",
            "username": "reader",
            "roles": ["allowed-role"],
            "roles_verified_at": verified_at,
            "exp": time.time() + 3600,
        }

    async def no_cached_member(user_id: str):
        return None

    monkeypatch.setattr(auth, "verify_jwt", verify_jwt)
    monkeypatch.setattr(auth, "_get_cached_member", no_cached_member)
    monkeypatch.setattr(
        auth,
        "_MEMBER_VERIFIER",
        FakeMemberVerifier(
            DiscordMemberVerificationDto(outcome="unavailable", status_code=429)
        ),
    )

    response = await auth.check_auth(_request_with_cookie())
    body = json.loads(response.body)

    assert response.status_code == 200
    assert body["loggedIn"] is True
    assert "Max-Age=0" not in response.headers.get("set-cookie", "")


@pytest.mark.asyncio
async def test_checkauth_returns_503_without_deleting_stale_session(monkeypatch):
    """身份组旧于 24 小时时拒绝降级，但保留 Cookie 等待恢复。"""
    _configure_auth(monkeypatch)
    verified_at = time.time() - auth.ROLE_VERIFICATION_TTL_SECONDS - 1

    async def verify_jwt(token: str, secret: str):
        return {
            "id": "123",
            "username": "reader",
            "roles": ["allowed-role"],
            "roles_verified_at": verified_at,
            "exp": time.time() + 3600,
        }

    async def no_cached_member(user_id: str):
        return None

    monkeypatch.setattr(auth, "verify_jwt", verify_jwt)
    monkeypatch.setattr(auth, "_get_cached_member", no_cached_member)
    monkeypatch.setattr(
        auth,
        "_MEMBER_VERIFIER",
        FakeMemberVerifier(
            DiscordMemberVerificationDto(
                outcome="unavailable", status_code=429, retry_after=5.0
            )
        ),
    )

    response = await auth.check_auth(_request_with_cookie())

    assert response.status_code == 503
    assert response.headers["Retry-After"] == "5"
    assert "set-cookie" not in response.headers


@pytest.mark.asyncio
async def test_checkauth_logs_out_only_for_definitive_not_member(monkeypatch):
    """明确不是服务器成员时才删除现有会话。"""
    _configure_auth(monkeypatch)

    async def verify_jwt(token: str, secret: str):
        return {
            "id": "123",
            "username": "reader",
            "roles": ["allowed-role"],
            "roles_verified_at": time.time(),
            "exp": time.time() + 3600,
        }

    async def no_cached_member(user_id: str):
        return None

    invalidations: list[tuple[str, str]] = []

    async def record_invalidation(user_id: str, reason: str):
        invalidations.append((user_id, reason))

    monkeypatch.setattr(auth, "verify_jwt", verify_jwt)
    monkeypatch.setattr(auth, "_get_cached_member", no_cached_member)
    monkeypatch.setattr(auth, "_delete_cached_member", record_invalidation)
    monkeypatch.setattr(
        auth,
        "_MEMBER_VERIFIER",
        FakeMemberVerifier(
            DiscordMemberVerificationDto(outcome="not_member", status_code=404)
        ),
    )

    response = await auth.check_auth(_request_with_cookie())

    assert response.status_code == 200
    assert json.loads(response.body)["loggedIn"] is False
    assert "session=" in response.headers["set-cookie"]
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert invalidations == [("123", "checkauth_not_member")]


@pytest.mark.asyncio
async def test_checkauth_logs_out_for_missing_required_role(monkeypatch):
    """实时成员信息缺少指定身份组时清除会话。"""
    _configure_auth(monkeypatch)

    async def verify_jwt(token: str, secret: str):
        return {
            "id": "123",
            "username": "reader",
            "roles": ["allowed-role"],
            "roles_verified_at": time.time(),
            "exp": time.time() + 3600,
        }

    async def no_cached_member(user_id: str):
        return None

    cache_writes: list[dict] = []
    invalidations: list[tuple[str, str]] = []

    async def record_cache_write(user_id: str, member: dict):
        cache_writes.append(member)

    async def record_invalidation(user_id: str, reason: str):
        invalidations.append((user_id, reason))

    monkeypatch.setattr(auth, "verify_jwt", verify_jwt)
    monkeypatch.setattr(auth, "_get_cached_member", no_cached_member)
    monkeypatch.setattr(auth, "_cache_member", record_cache_write)
    monkeypatch.setattr(auth, "_delete_cached_member", record_invalidation)
    monkeypatch.setattr(
        auth,
        "_MEMBER_VERIFIER",
        FakeMemberVerifier(
            DiscordMemberVerificationDto(
                outcome="verified",
                member={"roles": ["other-role"], "user": {"id": "123"}},
                status_code=200,
                token_alias="primary",
            )
        ),
    )

    response = await auth.check_auth(_request_with_cookie())

    assert response.status_code == 200
    assert json.loads(response.body)["loggedIn"] is False
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert cache_writes == []
    assert invalidations == [("123", "checkauth_missing_required_role")]
