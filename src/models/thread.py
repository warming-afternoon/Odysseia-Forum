from datetime import datetime, timezone
from typing import TYPE_CHECKING, List, Optional

from sqlalchemy import Index, event, func, inspect
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlmodel import JSON, BigInteger, Column, Field, Relationship, SQLModel

from models import ThreadTagLink
from shared.text_utils import build_search_vector_text

if TYPE_CHECKING:
    from models import Author, Tag, TagVote


class Thread(SQLModel, table=True):
    """帖子模型。"""

    __table_args__ = (
        Index("ix_thread_search_vector", "search_vector", postgresql_using="gin"),
    )

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

    search_vector: Optional[str] = Field(
        default=None,
        sa_column=Column(TSVECTOR, nullable=True),
        description="预计算的 PostgreSQL 全文搜索向量（rjieba 分词 + to_tsvector）",
    )
    """PostgreSQL 全文搜索向量"""

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


# ── search_vector 自动维护 ────────────────────────────


@event.listens_for(Thread, "before_insert")
def _on_thread_before_insert(mapper, connection, target: Thread):
    """INSERT 前自动填充 search_vector。"""
    tokens = build_search_vector_text(target.title, target.first_message_excerpt)
    target.search_vector = func.array_to_tsvector(tokens) if tokens else None


@event.listens_for(Thread, "before_update")
def _on_thread_before_update(mapper, connection, target: Thread):
    """仅当 title 或 first_message_excerpt 变化时重新计算 search_vector。"""
    insp = inspect(target)
    title_changed = insp.attrs.title.history.has_changes()
    excerpt_changed = insp.attrs.first_message_excerpt.history.has_changes()
    if not title_changed and not excerpt_changed:
        return
    tokens = build_search_vector_text(target.title, target.first_message_excerpt)
    target.search_vector = func.array_to_tsvector(tokens) if tokens else None