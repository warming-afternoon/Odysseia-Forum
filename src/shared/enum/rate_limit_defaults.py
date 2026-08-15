"""频率限制默认值"""

from enum import IntEnum


class RateLimitDefaults(IntEnum):
    """搜索和全局限流默认配置"""

    SEARCH_MAX_REQUESTS = 60
    """搜索接口每分钟最大请求数"""

    SIMILAR_MAX_REQUESTS = 60
    """相似推荐接口每分钟最大请求数"""

    GLOBAL_MAX_REQUESTS = 1500
    """全局限流每分钟最大请求数（所有 /v1/* 接口）"""

    WINDOW_SECONDS = 60
    """限流窗口时长（秒）"""

    WATCH_TTL_SECONDS = 600
    """watch 标记有效期（秒），10 分钟"""

    SUSPICIOUS_THRESHOLD = 600
    """全局每分钟请求数达到此值视为可疑行为"""
