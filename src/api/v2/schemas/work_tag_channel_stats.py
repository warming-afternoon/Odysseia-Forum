"""作品标签的主来源频道分组。"""

from pydantic import BaseModel


class WorkTagChannelStats(BaseModel):
    """平台和服务器参与频道身份判断，计数单位始终为作品。"""

    platform: str
    """作品主来源平台。"""

    guild_id: str
    """主来源服务器编号。"""

    channel_id: str
    """实际频道或虚拟标签挂载频道编号。"""

    channel_name: str | None = None
    """可用时提供频道名称，缺失时客户端显示编号。"""

    work_count: int
    """该分组中去重后的可见作品数。"""

    is_virtual: bool = False
    """是否为频道映射形成的虚拟分组。"""
