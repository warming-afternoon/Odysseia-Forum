from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlmodel import JSON, BigInteger, Column, Field, Relationship, SQLModel

from models import ThreadTagLink

if TYPE_CHECKING:
    from models import Author, Tag, TagVote


class Thread(SQLModel, table=True):
    """帖子模型。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    """数据库主键 ID"""

    guild_id: int = Field(
        default=0,
        sa_column=Column(BigInteger, index=True, nullable=False),
        description="帖子所属的 Discord 服务器 ID",
    )
    """帖子所属的 Discord 服务器 ID"""

    channel_id: int = Field(index=True)
    """帖子所在频道的 Discord ID"""

    thread_id: int = Field(index=True, unique=True)
    """帖子的 Discord ID"""

    title: str
    """帖子标题"""

    author_id: int = Field(index=True)
    """帖子作者的 Discord ID"""

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc), nullable=False, index=True
    )
    """帖子创建时间 (UTC)"""

    last_active_at: Optional[datetime] = Field(default=None, index=True)
    """帖子最后活跃时间 (UTC)"""

    reaction_count: int = Field(default=0, index=True)
    """帖子获得的总反应数"""

    reply_count: int = Field(default=0, index=True)
    """帖子的回复数量"""

    first_message_excerpt: Optional[str] = Field(default=None)
    """帖子首条消息的文本摘要"""

    thumbnail_urls: List[str] = Field(default_factory=list, sa_column=Column(JSON))
    """帖子首楼提取的图片链接列表"""

    latest_update_at: Optional[datetime] = Field(
        default=None, index=True, description="最新更新时间（贴主发布更新时刷新）"
    )
    """最新更新时间（贴主发布更新时刷新）"""

    latest_update_link: Optional[str] = Field(
        default=None, description="最新版消息链接"
    )
    """最新版消息链接"""

    collection_count: int = Field(default=0, description="被收藏次数")
    """帖子被收藏的总次数"""

    show_flag: bool = Field(
        default=True,
        index=True,
        nullable=False,
        description="帖子是否应出现在搜索结果中",
    )
    """帖子是否在搜索结果中显示"""

    not_found_count: int = Field(
        default=0,
        index=True,
        description="审计拉取帖子数据 NotFound 时 +1, 拉取成功时归零",
    )
    """拉取失败次数，大于 0 则搜索不到，大于5则删除"""

    display_count: int = Field(
        default=0,
        sa_column=Column(BigInteger, index=True),
        description="在搜索结果中的展示次数",
    )
    """在搜索结果中的总展示次数"""

    tags: List["Tag"] = Relationship(back_populates="threads", link_model=ThreadTagLink)
    """帖子关联的标签列表"""

    votes: List["TagVote"] = Relationship(back_populates="thread")
    """帖子关联的标签投票记录"""

    author: Optional["Author"] = Relationship(
        sa_relationship_kwargs={
            "primaryjoin": "Thread.author_id == Author.id",
            "foreign_keys": "[Thread.author_id]",
            "uselist": False,
            "lazy": "joined",
        }
    )
    """帖子作者的关系映射"""
