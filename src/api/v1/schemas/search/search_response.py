from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from ..base import PaginatedResponse
from .author import AuthorDetail

class ThreadDetail(BaseModel):
    """
    API 响应中单个帖子的详细信息模型
    """
    thread_id: int = Field(description="帖子的 Discord ID")
    channel_id: int = Field(description="帖子所在频道的 Discord ID")
    title: str = Field(description="帖子标题")
    author: Optional[AuthorDetail] = Field(description="帖子作者的详细信息")
    created_at: datetime = Field(description="帖子创建时间")
    last_active_at: Optional[datetime] = Field(description="帖子最后活跃时间")
    reaction_count: int = Field(description="帖子点赞数")
    reply_count: int = Field(description="帖子回复数")
    display_count: int = Field(description="在搜索结果中的展示次数")
    first_message_excerpt: Optional[str] = Field(description="帖子首条消息摘要")
    thumbnail_url: Optional[str] = Field(description="帖子缩略图 URL")
    tags: List[str] = Field(description="帖子关联的标签列表")

    class Config:
        from_attributes = True

class SearchResponse(PaginatedResponse[ThreadDetail]):
    """ 搜索接口的最终响应体 """
    pass