"""基于 Redis 的固定窗口频率限制器。

使用 INCR + EXPIRE 实现，每个用户独立计数，窗口到期后自动重置。
"""

import logging
from dataclasses import dataclass

from redis.asyncio import Redis

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """频率限制配置"""

    max_requests: int = 40
    window_seconds: int = 60
    key_prefix: str = "rate_limit"


@dataclass
class RateLimitResult:
    """频率限制检查结果"""

    allowed: bool
    current_count: int = 0
    remaining: int = 0
    reset_after: int = 0

    @classmethod
    def fail_open(cls) -> "RateLimitResult":
        """Redis 不可用时的降级结果：放行"""
        return cls(allowed=True, remaining=9999, reset_after=0)


async def check_rate_limit(
    redis: Redis,
    key: str,
    max_requests: int = 40,
    window_seconds: int = 60,
) -> RateLimitResult:
    """检查是否超出频率限制（固定窗口算法）。

    每次请求对 Redis key 执行 INCR。
    首次请求（INCR 返回 1）时设置 EXPIRE，窗口到期后 key 自动删除、计数归零。

    Args:
        redis: Redis 客户端。
        key: 限流的 Redis key（如 ``rate_limit:search:123``）。
        max_requests: 窗口内允许的最大请求数。
        window_seconds: 窗口时长（秒）。

    Returns:
        RateLimitResult: 包含是否允许、剩余次数、重置倒计时。
    """
    try:
        # 确保类型为纯 int（调用方可能传入 IntEnum，redis-py 部分版本不兼容）
        max_requests = int(max_requests)
        window_seconds = int(window_seconds)

        current = await redis.incr(key)

        if current == 1:
            # 首次请求，设置过期时间
            await redis.expire(key, window_seconds)
            ttl = window_seconds
        else:
            ttl = await redis.ttl(key)
            if ttl <= 0:
                # key 已存在但 TTL 丢失（极端情况），补设
                await redis.expire(key, window_seconds)
                ttl = window_seconds

        remaining = max(0, max_requests - current)

        return RateLimitResult(
            allowed=current <= max_requests,
            current_count=current,
            remaining=remaining,
            reset_after=ttl,
        )
    except Exception:
        logger.warning("Redis 频率限制检查失败，降级放行", exc_info=True)
        return RateLimitResult.fail_open()
