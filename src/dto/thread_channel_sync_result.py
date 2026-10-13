from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ThreadChannelSyncResult:
    """承载频道同步后的作者身份和缓存失效依据。"""

    author_id: int
    """数据库中解析后的作者 Discord ID，重建帖保留原始作者身份。"""

    tags_changed: bool
    """原生标签绑定是否变化，用于决定是否失效相似帖子候选缓存。"""

    visibility_changed: bool
    """帖子不存在计数是否由非零归零，用于决定是否失效可见性相关缓存。"""
