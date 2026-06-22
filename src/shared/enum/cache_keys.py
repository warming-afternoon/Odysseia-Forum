from enum import StrEnum


class CacheKeys(StrEnum):
    """Redis 缓存 Key 常量"""

    USER_PREFERENCES = "user_prefs:{user_id}:{guild_id}"
    """用户搜索偏好缓存"""

    FTS_TSQUERY_RESULT = "fts:tsquery:result:{prefix}:{hash}"
    """FTS tsquery 分词结果缓存（避免重复 jieba 分词）"""

    TOURNAMENT_THREAD = "tournament:thread:{thread_id}"
    """帖子赛事信息缓存"""

    RATE_LIMIT_WATCH = "rate_limit:watch:{user_id}"
    """限流触发后的 watch 标记，TTL 10 分钟"""

    def format(self, **kwargs) -> str:
        """填充 Key 中的占位符。"""
        return self.value.format(**kwargs)
