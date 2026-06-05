from enum import StrEnum


class CacheKeys(StrEnum):
    """Redis 缓存 Key 常量"""

    USER_PREFERENCES = "user_prefs:{user_id}:{guild_id}"
    """用户搜索偏好缓存"""

    def format(self, **kwargs) -> str:
        """填充 Key 中的占位符。"""
        return self.value.format(**kwargs)
