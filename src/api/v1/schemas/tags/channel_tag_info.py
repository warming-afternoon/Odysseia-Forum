from typing import Optional
from pydantic import BaseModel, Field, field_serializer

class ChannelTagInfo(BaseModel):
    """标签在某个频道下的统计信息"""

    guild_id: int = Field(description="频道所属服务器的 Discord ID")
    """频道所属服务器的 Discord ID"""

    guild_name: str = Field(default="未知服务器", description="服务器名称")
    """服务器名称"""

    channel_id: int = Field(description="频道的 Discord ID")
    """频道的 Discord ID"""

    channel_name: str = Field(default="未知频道", description="频道名称")
    """频道名称"""

    category_id: Optional[int] = Field(default=None, description="频道所属类别的 Discord ID")
    """频道所属类别的 Discord ID"""

    category_name: Optional[str] = Field(default=None, description="频道所属类别名称")
    """频道所属类别名称"""

    tag_id: int = Field(description="标签的 Discord ID（虚拟标签为 0）")
    """标签的 Discord ID（虚拟标签为 0）"""

    thread_count: int = Field(description="该频道下包含此标签的帖子数量")
    """该频道下包含此标签的帖子数量"""

    is_virtual: bool = Field(description="是否为虚拟映射标签")
    """是否为虚拟映射标签"""

    @field_serializer("guild_id", "channel_id", "tag_id", "category_id")
    def serialize_ids(self, value: Optional[int]) -> Optional[str]:
        """将 Discord ID 序列化为字符串，避免 JavaScript 精度丢失"""
        return str(value) if value is not None else None
