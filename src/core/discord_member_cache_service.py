import logging
import time

import orjson

from shared.enum.constant_enum import ConstantEnum
from shared.redis_client import RedisManager

logger = logging.getLogger(__name__)


class DiscordMemberCacheService:
    """读写登录流程与身份展示共用的 Redis 成员快照。"""

    async def get_member(self, user_id: str) -> dict | None:
        """读取成员缓存并兼容没有验证时间的历史快照。"""
        try:
            # 按用户 ID 读取成员快照；身份组变更或退服事件会删除该键。
            client = RedisManager.get_client()
            cache_key = f"user:discord:{user_id}"
            raw = await client.get(cache_key)
            if not raw:
                return None
            cached = orjson.loads(raw)
            if not isinstance(cached, dict):
                return None

            # 缺少验证时间时，根据剩余 TTL 推算；读取不延长缓存有效期。
            if "roles_verified_at" not in cached:
                remaining_ttl = await client.ttl(cache_key)
                lifetime = int(ConstantEnum.AUTH_CACHE_TTL)
                if 0 <= remaining_ttl <= lifetime:
                    cached["roles_verified_at"] = time.time() - (
                        lifetime - remaining_ttl
                    )
            return cached
        except Exception:
            logger.warning("读取用户缓存失败", exc_info=True)
            return None

    async def get_roles(self, user_id: str) -> set[str] | None:
        """返回有效快照中的身份组，缺失或异常时交给调用方实时查询。"""
        cached = await self.get_member(user_id)
        if cached is None:
            return None

        # 空身份组列表也是有效结果，不能当成缓存未命中。
        roles = cached.get("roles")
        if not isinstance(roles, list) or any(
            not isinstance(role, (str, int)) for role in roles
        ):
            return None
        try:
            verified_at = float(cached.get("roles_verified_at", 0))
        except (TypeError, ValueError):
            return None

        # 校验身份组验证时间，避免使用超过认证有效期或时间异常的快照。
        now = time.time()
        if not 0 < verified_at <= now:
            return None
        if now - verified_at > int(ConstantEnum.AUTH_CACHE_TTL):
            return None
        return {str(role) for role in roles}

    async def set_member(self, user_id: str, member: dict) -> None:
        """写入已通过论坛身份组校验的成员快照，缓存故障不阻断当前请求。"""
        try:
            # 成员快照按用户 ID 存储，由身份组变更和退服事件删除。
            await RedisManager.get_client().setex(
                f"user:discord:{user_id}",
                int(ConstantEnum.AUTH_CACHE_TTL),
                orjson.dumps(member).decode(),
            )
        except Exception:
            logger.warning("写入用户缓存失败", exc_info=True)
