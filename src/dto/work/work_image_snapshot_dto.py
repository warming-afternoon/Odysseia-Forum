"""图片补抓使用的无会话来源快照。"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkImageSnapshotDTO:
    """网络请求期间不持有数据库对象或行锁。"""

    work_id: int
    """作品编号。"""

    thread_row_id: int
    """来源帖内部编号。"""

    platform: str
    """来源平台。"""

    guild_id: str
    """来源服务器编号。"""

    channel_id: str
    """来源父频道编号。"""

    thread_id: str
    """来源主帖及首消息编号。"""

    version: int
    """来源投影版本。"""

    images: tuple[str, ...]
    """读取时的作品图片，用于并发比较。"""

    thread_images: tuple[str, ...]
    """读取时的来源图片，用于并发比较。"""
