from typing import List, Optional, TYPE_CHECKING
from sqlmodel import Field, SQLModel, Relationship
from datetime import datetime

from .thread_tag_link import ThreadTagLink

if TYPE_CHECKING:
    from .tag import Tag
    from .tag_vote import TagVote


class Thread(SQLModel, table=True):
    """帖子模型。"""

    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: int = Field(index=True)
    thread_id: int = Field(index=True, unique=True)
    title: str
    author_id: int = Field(index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)

    last_active_at: Optional[datetime] = Field(default=None, index=True)
    reaction_count: int = Field(default=0, index=True)
    reply_count: int = Field(default=0, index=True)
    first_message_excerpt: Optional[str] = Field(default=None)
    thumbnail_url: Optional[str] = Field(default=None)
    
    # 审计用字段
    # fetch_channel 抛出 NotFound 时 +1，拉取成功时归零
    # 大于 0 则搜索不到，大于 5 则删除
    not_found_count: int = Field(default=0, index=True) 

    tags: List["Tag"] = Relationship(back_populates="threads", link_model=ThreadTagLink)
    votes: List["TagVote"] = Relationship(back_populates="thread")
