from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger
from sqlmodel import Column, Field, SQLModel


class Booklist(SQLModel, table=True):
    """书单元数据"""

    __tablename__ = "booklist"  # type: ignore

    id: Optional[int] = Field(default=None, primary_key=True)
    """主键ID"""

    owner_id: int = Field(
        sa_column=Column(BigInteger, index=True),
        description="创建该书单的用户Discord ID",
    )
    """创建该书单的用户 Discord ID"""

    title: str = Field(index=True, description="书单标题")
    """书单标题"""

    description: Optional[str] = Field(default=None, description="书单简介")
    """书单简介"""

    # 封面图，可以是书单内第一个帖子的图，也可以是自定义
    cover_image_url: Optional[str] = Field(default=None, description="书单封面")
    """书单封面"""

    # 状态字段
    is_public: bool = Field(default=True, index=True, description="是否公开")
    """是否公开"""

    is_anonymous: bool = Field(default=False, index=True, description="是否匿名")
    """是否匿名"""

    is_default: bool = Field(
        default=False, index=True, description="是否为用户的默认书单"
    )
    """是否为用户的默认书单"""

    is_tournament: bool = Field(default=False, index=True, description="是否为赛事书单")
    """是否为赛事书单"""

    tournament_channel_id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, index=True, unique=True),
        description="赛事关联的 Discord 频道ID",
    )
    """赛事关联的 Discord 频道ID"""

    display_type: int = Field(
        default=1, description="展示方式: 1-加入时间倒序, 2-display_order"
    )
    """展示方式: 1-加入时间倒序, 2-display_order"""

    # 统计数据
    item_count: int = Field(default=0, description="书单内帖子数量")
    """书单内帖子数量"""

    view_count: int = Field(default=0, description="被浏览次数")
    """被浏览次数"""

    collection_count: int = Field(default=0, description="被收藏次数")
    """被收藏次数"""

    # Discord 展示位置
    display_thread_id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger),
        description="展示帖子的ID",
    )
    """展示帖子的 Discord ID"""

    display_channel_id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger),
        description="展示频道的ID",
    )
    """展示频道的 Discord ID"""

    display_guild_id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger),
        description="展示服务器的ID",
    )
    """展示服务器的 Discord ID"""

    created_at: datetime = Field(
        default_factory=datetime.utcnow, description="创建时间"
    )
    """创建时间"""

    updated_at: datetime = Field(
        default_factory=datetime.utcnow,
        sa_column_kwargs={"onupdate": datetime.utcnow},
        description="最后更新时间",
    )
    """最后更新时间"""
