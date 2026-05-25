from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_serializer
from api.v1.schemas.search.author_detail import AuthorDetail


class BooklistDetail(BaseModel):
    """书单详情"""

    id: int = Field(description="书单ID")
    """书单ID"""

    owner_id: int = Field(description="创建者用户ID")
    """创建者用户ID"""

    title: str = Field(description="书单标题")
    """书单标题"""

    description: Optional[str] = Field(None, description="书单简介")
    """书单简介"""

    cover_image_url: Optional[str] = Field(None, description="书单封面图URL")
    """书单封面图URL"""

    author: Optional[AuthorDetail] = Field(None, description="创建者信息")
    """创建者信息"""

    is_public: bool = Field(description="是否公开")
    """是否公开"""

    is_anonymous: bool = Field(description="是否匿名")
    """是否匿名"""

    is_default: bool = Field(description="是否为用户的默认书单")
    """是否为用户的默认书单"""

    is_tournament: bool = Field(False, description="是否为赛事书单")
    """是否为赛事书单"""

    tournament_channel_id: Optional[int] = Field(None, description="赛事频道ID")
    """赛事频道ID"""

    display_type: int = Field(
        description="展示方式: 1=加入时间倒序, 2=作者自定义排序(display_order)"
    )
    """展示方式: 1=加入时间倒序, 2=作者自定义排序(display_order)"""

    item_count: int = Field(description="书单内帖子数量")
    """书单内帖子数量"""

    collection_count: int = Field(description="被收藏次数")
    """被收藏次数"""

    view_count: int = Field(description="被浏览次数")
    """被浏览次数"""

    created_at: datetime = Field(description="创建时间")
    """创建时间"""

    updated_at: datetime = Field(description="最后更新时间")
    """最后更新时间"""

    collected_flag: bool = Field(False, description="当前用户是否收藏了该书单")
    """当前用户是否收藏了该书单"""

    @field_serializer("owner_id")
    def serialize_owner_id(self, value: int) -> str:
        """将 Discord ID 序列化为字符串，避免 JavaScript 精度丢失"""
        return str(value)

    class Config:
        from_attributes = True
