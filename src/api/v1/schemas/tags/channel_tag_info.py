from pydantic import BaseModel, Field, field_serializer


class ChannelTagInfo(BaseModel):
    """标签在某个频道下的统计信息"""

    channel_id: int = Field(description="频道的 Discord ID")
    """频道的 Discord ID"""

    tag_id: int = Field(description="标签的 Discord ID（虚拟标签为 0）")
    """标签的 Discord ID（虚拟标签为 0）"""

    thread_count: int = Field(description="该频道下包含此标签的帖子数量")
    """该频道下包含此标签的帖子数量"""

    is_virtual: bool = Field(description="是否为虚拟映射标签")
    """是否为虚拟映射标签"""

    @field_serializer("channel_id", "tag_id")
    def serialize_id(self, value: int) -> str:
        """将 Discord ID 序列化为字符串，避免 JavaScript 精度丢失"""
        return str(value)
