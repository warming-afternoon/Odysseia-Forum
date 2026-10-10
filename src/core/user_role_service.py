import time

import httpx

from core.discord_member_cache_service import DiscordMemberCacheService
from core.tag_access_service import TagAccessService
from dto.meta.user_role import UserRole
from shared.tag_error import TagError


class UserRoleService:
    """复用共享权限核验逻辑，独立查询用户的两种管理身份。"""

    def __init__(self, config: dict):
        self.access = TagAccessService(config)
        self.member_cache = DiscordMemberCacheService()
        self.management_role_id = str(config.get("management_role_id") or "")
        auth_config = config.get("auth", {})
        self.auth_guild_id = str(auth_config.get("guild_id") or "")
        role_ids = auth_config.get("role_ids") or []
        role_ids = role_ids.split(",") if isinstance(role_ids, str) else role_ids
        self.required_role_ids = {str(role_id).strip() for role_id in role_ids}

    async def get_role(self, user_id: int) -> UserRole:
        """检查用户 ID 是否在 BOT 管理员名单中，以及用户是否拥有主服务器的管理身份组。"""
        # 判定是否为 BOT 管理员
        role = UserRole(
            is_management_member=False,
            is_bot_admin=self.access.bot_admin(user_id),
        )

        # 未配置管理组时，无需查询服务器成员身份组即可判定非管理组。
        if not self.management_role_id:
            return role

        try:
            roles = await self._get_member_roles(user_id)
        except TagError as exc:
            # 用户不在服务器中时，管理组身份为 False；
            if exc.status == 403:
                return role
            raise
        except (httpx.RequestError, ValueError) as exc:
            raise TagError(
                "permission_unavailable", "暂时无法查询用户管理身份", 503
            ) from exc

        role.is_management_member = self.management_role_id in roles
        return role

    async def _get_member_roles(self, user_id: int) -> set[str]:
        """先尝试从 Redis 获取用户身份组；没有可用缓存时向 Discord 查询。"""
        # Redis 缓存的是认证配置指定的 Discord 服务器成员，查询其他服务器时不能使用。
        can_share_cache = (self.auth_guild_id == str(self.access.main_guild))
        roles = (
            await self.member_cache.get_roles(str(user_id)) if can_share_cache else None
        )

        # 空身份组集合也是有效缓存，只有 None 表示需要实时查询。
        if roles is not None:
            return roles

        member = await self.access.get_member(user_id, self.access.main_guild)
        roles = {str(role_id) for role_id in member["roles"]}
        if can_share_cache:
            await self._cache_member(user_id, member, roles)
        return roles

    async def _cache_member(self, user_id: int, member: dict, roles: set[str]) -> None:
        """将具备论坛访问资格的成员资料写入认证缓存。"""
        # 认证接口使用该缓存判断访问资格，不缓存缺少访问身份组的成员。
        if not roles.intersection(self.required_role_ids):
            return

        member_user = member.get("user")
        if not isinstance(member_user, dict) or str(member_user.get("id")) != str(
            user_id
        ):
            raise ValueError("Discord 成员用户资料格式错误")

        # 保存用户资料和身份组验证时间，供认证接口读取完整成员快照。
        await self.member_cache.set_member(
            str(user_id),
            {
                "roles": sorted(roles),
                "user": member_user,
                "roles_verified_at": time.time(),
            },
        )
