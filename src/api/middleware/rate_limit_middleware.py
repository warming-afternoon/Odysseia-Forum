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
from shared.rate_limiter import check_rate_limit
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
        result = await check_rate_limit(
            redis=RedisManager.get_client(),
            key=key,
            max_requests=RateLimitDefaults.GLOBAL_MAX_REQUESTS,
            window_seconds=RateLimitDefaults.WINDOW_SECONDS,
        )

        if not result.allowed:
            logger.warning(
                "全局接口触发频率限制: user_id=%s, count=%s/%s, reset=%ss | "
                "method=%s path=%s client=%s ua=%s cf_ip=%s",
                user_id,
                result.current_count,
                RateLimitDefaults.GLOBAL_MAX_REQUESTS,
                result.reset_after,
                scope.get("method", "-"),
                path,
                scope.get("client", ("-", 0))[0],
                self._get_header(scope, "user-agent"),
                self._get_header(scope, "cf-connecting-ip"),
            )
            await self._send_429(send, result.reset_after)
            return

        # 放行，注入限流状态头
        await self._send_with_headers(scope, receive, send, result)

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
