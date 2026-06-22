"""全局限流 ASGI 中间件。

对所有 /v1/* 请求按 user_id 做 Redis 固定窗口计数，
白名单路径（/v1/health、/v1/auth、/v1/debug）跳过。
"""

import json
import logging
from typing import Optional
from urllib.parse import parse_qsl

from api.v1.utils.jwt_utils import verify_jwt
from shared.enum.rate_limit_defaults import RateLimitDefaults
from shared.rate_limit import (
    check_rate_limit,
    is_user_watched,
    set_rate_limit_watch,
)
from shared.redis_client import RedisManager

logger = logging.getLogger(__name__)

# 不限流的路径前缀
_SKIP_PREFIXES = ("/v1/health", "/v1/auth", "/v1/debug")

# Redis key 前缀
_KEY_PREFIX = "rate_limit:global"


class RateLimitMiddleware:
    """全局请求频率限制中间件。

    在 CORS 之后、路由之前执行。
    Redis 不可用时 fail-open，不影响正常请求。
    """

    def __init__(self, app):
        self.app = app
        self._jwt_secret: Optional[str] = None
        self._load_jwt_secret()

    def _load_jwt_secret(self) -> None:
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                config = json.load(f)
            self._jwt_secret = config.get("auth", {}).get("jwt_secret")
        except Exception:
            logger.warning("全局限流中间件：无法加载 JWT secret", exc_info=True)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        if path.startswith(_SKIP_PREFIXES):
            await self.app(scope, receive, send)
            return

        user_id = await self._extract_user_id(scope)
        if not user_id:
            await self.app(scope, receive, send)
            return

        key = f"{_KEY_PREFIX}:{user_id}"
        redis = RedisManager.get_client()
        result = await check_rate_limit(
            redis=redis,
            key=key,
            max_requests=RateLimitDefaults.GLOBAL_MAX_REQUESTS,
            window_seconds=RateLimitDefaults.WINDOW_SECONDS,
        )

        if not result.allowed:
            # 读取 body 用于日志（不需要 replay，直接返回 429）
            body_bytes, _ = await self._read_and_replay_body(receive)

            logger.warning(
                "全局接口触发频率限制: user_id=%s, count=%s/%s, reset=%ss | "
                "method=%s path=%s ua=%s body=%s",
                user_id,
                result.current_count,
                RateLimitDefaults.GLOBAL_MAX_REQUESTS,
                result.reset_after,
                scope.get("method", "-"),
                path,
                self._get_header(scope, "user-agent"),
                self._truncate_body(body_bytes),
            )
            await set_rate_limit_watch(redis, user_id)
            await self._send_429(send, result.reset_after)
            return

        # === 放行分支 ===

        # 可疑阈值检查（在放行后执行，避免与上方限流 WARNING 重复打印；
        # is_user_watched 守卫确保每个用户每 10 分钟仅一次）
        if (
            result.current_count >= int(RateLimitDefaults.SUSPICIOUS_THRESHOLD)
            and not await is_user_watched(redis, user_id)
        ):
            logger.warning(
                "全局可疑高频请求: user_id=%s, count=%s/%s | "
                "method=%s path=%s ua=%s",
                user_id,
                result.current_count,
                RateLimitDefaults.GLOBAL_MAX_REQUESTS,
                scope.get("method", "-"),
                path,
                self._get_header(scope, "user-agent"),
            )
            await set_rate_limit_watch(redis, user_id)

        # WATCH 日志（含 body buffer-replay）
        if await is_user_watched(redis, user_id):
            body_bytes, replay_receive = await self._read_and_replay_body(receive)
            logger.info(
                "WATCH: user_id=%s method=%s path=%s ua=%s body=%s",
                user_id,
                scope.get("method", "-"),
                path,
                self._get_header(scope, "user-agent"),
                self._truncate_body(body_bytes),
            )
            await self._send_with_headers(scope, replay_receive, send, result)
            return

        # 放行，注入限流状态头
        await self._send_with_headers(scope, receive, send, result)

    @staticmethod
    async def _read_and_replay_body(receive):
        """读取 ASGI body 并返回一个可重放的 receive。

        仅对 watched 用户调用，确保下游 FastAPI 仍能正常读取 body。
        """
        body_chunks = []
        more_body = True
        while more_body:
            message = await receive()
            body_chunks.append(message.get("body", b""))
            more_body = message.get("more_body", False)

        body_bytes = b"".join(body_chunks)
        replayed = False

        async def _replay_receive():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {
                    "type": "http.request",
                    "body": body_bytes,
                    "more_body": False,
                }
            return await receive()

        return body_bytes, _replay_receive

    @staticmethod
    def _truncate_body(body_bytes: bytes, max_len: int = 512) -> str:
        """截断 body 用于日志输出。"""
        if not body_bytes:
            return "-"
        return body_bytes.decode("utf-8", errors="replace")[:max_len]

    @staticmethod
    def _get_header(scope, name: str) -> str:
        """从 ASGI scope 中读取指定 header，找不到返回 '-'。"""
        for h_name, h_value in scope.get("headers", []):
            if h_name.decode("latin-1").lower() == name.lower():
                return h_value.decode("latin-1", errors="replace")[:256]
        return "-"

    async def _extract_user_id(self, scope) -> Optional[str]:
        """从请求中解析 JWT 并提取 user_id。解析失败返回 None。"""
        if not self._jwt_secret:
            return None

        token = None

        # 1) 解析 headers 中的 Authorization Bearer
        for name, value in scope.get("headers", []):
            name = name.decode("latin-1").lower()
            if name == "authorization" and value:
                val = value.decode("latin-1")
                if val.startswith("Bearer "):
                    token = val[7:]
                    break

        # 2) 回退到 cookie
        if not token:
            for name, value in scope.get("headers", []):
                if name.decode("latin-1").lower() == "cookie":
                    cookie_str = value.decode("latin-1")
                    for item in cookie_str.split(";"):
                        item = item.strip()
                        if item.startswith("session="):
                            token = item[8:]
                            break
                    break

        if not token:
            return None

        payload = await verify_jwt(token, self._jwt_secret)
        return payload.get("id") if payload else None

    async def _send_429(self, send, reset_after: int) -> None:
        body = '{"detail":"请求过于频繁，请稍后重试"}'.encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 429,
            "headers": [
                (b"content-type", b"application/json"),
                (b"retry-after", str(reset_after).encode()),
                (b"x-ratelimit-remaining", b"0"),
                (b"x-ratelimit-reset", str(reset_after).encode()),
            ],
        })
        await send({
            "type": "http.response.body",
            "body": body,
        })

    async def _send_with_headers(self, scope, receive, send, result) -> None:
        """包装 send 以注入限流响应头。"""
        headers_injected = False

        async def _send(message):
            nonlocal headers_injected
            if not headers_injected and message["type"] == "http.response.start":
                message["headers"].extend([
                    (b"x-ratelimit-remaining", str(result.remaining).encode()),
                    (b"x-ratelimit-reset", str(result.reset_after).encode()),
                ])
                headers_injected = True
            await send(message)

        await self.app(scope, receive, _send)
