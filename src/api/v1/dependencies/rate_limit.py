"""搜索接口频率限制依赖。

按 JWT 中的 user_id 维度限流，Redis 固定窗口算法。
"""

import logging
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Response, status

from api.v1.dependencies.security import get_current_user
from shared.rate_limiter import RateLimitConfig, check_rate_limit
from shared.redis_client import RedisManager

logger = logging.getLogger(__name__)

# 模块级配置，由 initialize_rate_limit() 在启动时设置
_search_config: Optional[RateLimitConfig] = None


def initialize_rate_limit(config: Optional[dict] = None) -> None:
    """从 config.json 加载搜索接口频率限制配置（启动时调用）。"""
    global _search_config

    raw = (config or {}).get("api", {}).get("rate_limit", {}).get("search", {}) or {}

    _search_config = RateLimitConfig(
        max_requests=raw.get("max_requests", 40),
    )
    logger.info(
        "搜索接口频率限制已初始化: %s次/%s秒",
        _search_config.max_requests,
        _search_config.window_seconds,
    )


async def search_rate_limit(
    response: Response,
    current_user: Optional[Dict[str, Any]] = Depends(get_current_user),
) -> None:
    """对搜索接口按 user_id 进行频率限制。

    作为 router 级 ``Depends`` 使用，配合 ``require_auth`` 确保用户已认证。
    ``get_current_user`` 在同一请求中只会执行一次（FastAPI 依赖缓存）。

    Raises:
        HTTPException(429): 超出频率限制时，附带 Retry-After 头。
    """
    config = _search_config
    if config is None:
        return  # 未初始化（极端情况），静默放行

    user_id = current_user.get("id") if current_user else None
    if not user_id:
        return  # 防御：require_auth 已先行拒绝未认证用户

    redis = RedisManager.get_client()
    key = f"{config.key_prefix}:search:{user_id}"

    result = await check_rate_limit(
        redis=redis,
        key=key,
        max_requests=config.max_requests,
        window_seconds=config.window_seconds,
    )

    # 无论是否超限都返回当前状态头
    response.headers["X-RateLimit-Remaining"] = str(result.remaining)
    response.headers["X-RateLimit-Reset"] = str(result.reset_after)

    if not result.allowed:
        logger.warning(
            "搜索接口触发频率限制: user_id=%s, count=%s/%s, reset=%ss",
            user_id,
            result.current_count,
            config.max_requests,
            result.reset_after,
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="请求过于频繁，请稍后重试",
            headers={"Retry-After": str(result.reset_after)},
        )
