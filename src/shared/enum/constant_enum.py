from enum import IntEnum


class ConstantEnum(IntEnum):
    """通用常量"""

    DEFAULT_SURGE_DAYS = 30
    """默认统计天数"""

    MAX_SURGE_DAYS = 90
    """最大统计天数"""

    TREND_CACHE_EXPIRE_SECONDS = 300
    """趋势缓存过期时间（秒）"""

    CHANNELS_CACHE_EXPIRE_SECONDS = 1800
    """频道元数据缓存过期时间（秒）—— 30 分钟"""

    STATISTICS_THRESHOLD_DAYS = 60
    """只有最近 60 天内创建的帖子才计入趋势统计"""
