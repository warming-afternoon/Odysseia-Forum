"""在 Redis 中绑定 Discord OAuth 回调、处理进度和一次性交付结果。"""

import asyncio
import hashlib

import orjson
from redis.asyncio import Redis

OAUTH_STATE_TTL_SECONDS = 600
OAUTH_OWNER_TTL_SECONDS = 60
LOGIN_CLAIM_TTL_SECONDS = 60

_BEGIN = """
local ttl = redis.call('PTTL', KEYS[1])
local state = redis.call('GET', KEYS[1])
if ttl <= 0 or (state ~= ARGV[1] and state ~= ARGV[2] and state ~= ARGV[3]) then
    return {'invalid'}
end

local transaction = redis.call('HGET', KEYS[2], 'transaction')
if not transaction then
    if state == ARGV[3] or redis.call('EXISTS', KEYS[4]) == 1
        or redis.call('EXISTS', KEYS[5]) == 1 then
        return {'invalid'}
    end
    redis.call('SET', KEYS[1], ARGV[3], 'PX', ttl)
    redis.call('HSET', KEYS[2], 'transaction', ARGV[4])
    redis.call('EXPIRE', KEYS[2], ARGV[6])
    redis.call('SET', KEYS[3], ARGV[5], 'PX', math.min(ttl, tonumber(ARGV[7])))
    return {'new'}
end
if transaction ~= ARGV[4] or state ~= ARGV[3] then
    return {'invalid'}
end

local delivery = redis.call('HGET', KEYS[2], 'delivery')
if delivery then
    local claim_key = redis.call('HGET', KEYS[2], 'claim_key')
    if claim_key and redis.call('EXISTS', claim_key) == 0 then
        return {'expired'}
    end
    return {'complete', delivery}
end
if redis.call('EXISTS', KEYS[3]) == 1 then
    return {'busy'}
end
local progress = redis.call('HGET', KEYS[2], 'progress')
if not progress then
    return {'expired'}
end
redis.call('SET', KEYS[3], ARGV[5], 'PX', math.min(ttl, tonumber(ARGV[7])))
return {'resume', progress}
"""

_SAVE = """
local ttl = redis.call('PTTL', KEYS[1])
if ttl <= 0 or redis.call('EXISTS', KEYS[2]) == 0 or redis.call('GET', KEYS[3]) ~= ARGV[1] then
    return 0
end
if ARGV[2] == 'progress' then
    redis.call('HSET', KEYS[2], 'progress', ARGV[3])
    redis.call('PEXPIRE', KEYS[3], math.min(ttl, tonumber(ARGV[6])))
    return 1
end
if ARGV[4] ~= '' then
    local claim_ttl = math.min(ttl, tonumber(ARGV[5]))
    if not redis.call('SET', KEYS[4], ARGV[4], 'PX', claim_ttl, 'NX') then
        return 0
    end
    redis.call('HSET', KEYS[2], 'claim_key', KEYS[4])
end
redis.call('HSET', KEYS[2], 'delivery', ARGV[3])
redis.call('HDEL', KEYS[2], 'progress')
redis.call('DEL', KEYS[3])
return 1
"""

_RELEASE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""


class OAuthCallbackCache:
    """协调同一浏览器、state 和 code 绑定的回调。"""

    def __init__(self, redis_client: Redis):
        """使用现有 Redis 客户端初始化缓存。"""
        self._redis = redis_client

    async def begin(
        self, state_key: str, binding: str, mode: str, code: str, owner: str
    ) -> tuple[str, dict | None]:
        """原子认领首次回调，或等待同一事务的处理进度。"""
        # 兼容升级前未使用的 state，同时把模式和 code 固定在第一次认领中。
        bound_browser = f"{binding}:{mode}"
        claimed_state = f"{bound_browser}:{code}"
        transaction = f"{state_key}:{bound_browser}"
        deadline = asyncio.get_running_loop().time() + 10
        while True:
            outcome, *payload = await self._redis.eval(
                _BEGIN,
                5,
                state_key,
                f"forum:oauth:code:{code}",
                f"forum:oauth:owner:{code}",
                f"auth:oauth:state:{code}",
                f"auth:oauth:lock:{code}",
                binding,
                bound_browser,
                claimed_state,
                transaction,
                owner,
                OAUTH_STATE_TTL_SECONDS,
                OAUTH_OWNER_TTL_SECONDS * 1000,
            )
            if outcome != "busy" or asyncio.get_running_loop().time() >= deadline:
                return outcome, orjson.loads(payload[0]) if payload else None
            await asyncio.sleep(0.1)

    async def save(
        self,
        state_key: str,
        code: str,
        owner: str,
        payload: dict,
        *,
        delivery: bool = False,
        claim: dict | None = None,
    ) -> None:
        """在事务仍有效时保存进度，或原子发布交付结果及其登录码。"""
        # 登录码和可见的完成状态一起发布，且 claim 过期不会重建。
        claim_key = (
            "forum:login:claim:"
            + hashlib.sha256(payload["login_code"].encode()).hexdigest()
            if claim is not None
            else state_key
        )
        saved = await self._redis.eval(
            _SAVE,
            4,
            state_key,
            f"forum:oauth:code:{code}",
            f"forum:oauth:owner:{code}",
            claim_key,
            owner,
            "delivery" if delivery else "progress",
            orjson.dumps(payload),
            orjson.dumps(claim) if claim is not None else "",
            LOGIN_CLAIM_TTL_SECONDS * 1000,
            OAUTH_OWNER_TTL_SECONDS * 1000,
        )
        if not saved:
            raise RuntimeError("OAuth 登录事务已失效")

    async def release(self, code: str, owner: str) -> None:
        """只释放当前回调持有的处理权，保留 code 与原事务的绑定。"""
        await self._redis.eval(_RELEASE, 1, f"forum:oauth:owner:{code}", owner)
