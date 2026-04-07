"""Banner项目Schema"""

from pydantic import BaseModel, Field, field_serializer


class BannerItem(BaseModel):
    """Banner轮播项"""

    thread_id: int
    title: str
    cover_image_url: str
    channel_id: int
    guild_id: int = Field(
        default=0,
        description="帖子所属服务器 ID（从索引帖读取，用于前端生成 Discord 链接）",
    )

    class Config:
        from_attributes = True

    @field_serializer("thread_id", "channel_id", "guild_id")
    def serialize_id(self, value: int) -> str:
        """将 Discord ID 序列化为字符串，避免 JavaScript 精度丢失"""
        return str(value)
