from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field, field_serializer

from api.v1.schemas.search.author_detail import AuthorDetail


class TournamentInfo(BaseModel):
    """赛事书单简要信息"""

    booklist_id: int = Field(description="赛事书单ID")
    """赛事书单ID"""

    booklist_name: str = Field(description="赛事书单名")
    """赛事书单名"""

    @field_serializer("booklist_id")
    def serialize_id(self, value: int) -> str:
        """将 Discord ID 序列化为字符串，避免 JavaScript 精度丢失"""
        return str(value)


class ThreadDetail(BaseModel):
    """API 响应中单个帖子的详细信息模型"""

    thread_id: int = Field(description="帖子的 Discord ID")
    """帖子的 Discord ID"""

    guild_id: int = Field(default=0, description="帖子所属的 Discord 服务器 ID")
    """帖子所属的 Discord 服务器 ID"""

    channel_id: int = Field(description="帖子所在频道的 Discord ID")
    """帖子所在频道的 Discord ID"""

    title: str = Field(description="帖子标题")
    """帖子标题"""

    author: Optional[AuthorDetail] = Field(
        default=None, description="帖子作者的详细信息"
    )
    """帖子作者的详细信息"""

    created_at: datetime = Field(description="帖子创建时间")
    """帖子创建时间"""

    last_active_at: Optional[datetime] = Field(
        default=None, description="帖子最后活跃时间"
    )
    """帖子最后活跃时间"""

    reaction_count: int = Field(description="帖子点赞数")
    """帖子点赞数"""

    reply_count: int = Field(description="帖子回复数")
    """帖子回复数"""

    collection_count: int = Field(default=0, description="帖子被收藏的总次数")
    """帖子被收藏的总次数"""

    display_count: int = Field(default=0, description="在搜索结果中的展示次数")
    """在搜索结果中的展示次数"""

    first_message_excerpt: Optional[str] = Field(
        default=None, description="帖子首条消息摘要"
    )
    """帖子首条消息摘要"""

    thumbnail_urls: List[str] = Field(description="帖子首楼图片URL列表")
    """帖子首楼图片URL列表"""

    tags: List[str] = Field(default_factory=list, description="帖子关联的标签列表")
    """帖子关联的标签列表"""

    virtual_tags: List[str] = Field(
        default_factory=list, description="帖子匹配的虚拟映射标签名列表"
    )
    """帖子匹配的虚拟映射标签名列表"""

    collected_flag: bool = Field(default=False, description="当前用户是否收藏了该帖子")
    """当前用户是否收藏了该帖子"""

    is_tournament: bool = Field(
        default=False, description="该帖子是否为参赛帖子"
    )
    """该帖子是否为参赛帖子"""

    tournament_info_list: List["TournamentInfo"] = Field(
        default_factory=list, description="所属赛事书单信息列表"
    )
    """所属赛事书单信息列表"""

    @field_serializer("thread_id", "guild_id", "channel_id")
    def serialize_id(self, value: int) -> str:
        """将 Discord ID 序列化为字符串，避免 JavaScript 精度丢失"""
        return str(value)

    class Config:
        from_attributes = True
