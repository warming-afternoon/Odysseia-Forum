from enum import Enum, IntEnum


class SearchConfigDefaults(float, Enum):
    """搜索配置默认值"""
    UCB1_EXPLORATION_FACTOR = 1.414  # sqrt(2)
    STRENGTH_WEIGHT = 10.0


class SearchConfigType(IntEnum):
    """搜索配置类型枚举"""
    TOTAL_DISPLAY_COUNT = 1  # 总展示次数 (N)
    UCB1_EXPLORATION_FACTOR = 2  # UCB1 探索因子 (C)
    STRENGTH_WEIGHT = 3  # 实力分权重 (W)
    NOTIFY_ON_MUTEX_CONFLICT = 4 #互斥标签冲突通知开关