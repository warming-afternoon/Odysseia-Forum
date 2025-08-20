from typing import List, Optional, TYPE_CHECKING, Any
from sqlmodel import Field, SQLModel, Relationship
from sqlalchemy import JSON, Column
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
    author_id: int
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    
    last_active_at: Optional[datetime] = Field(default=None, index=True)
    reaction_count: int = Field(default=0)
    reply_count: int = Field(default=0)
    first_message_excerpt: Optional[str] = Field(default=None)
    thumbnail_url: Optional[str] = Field(default=None)
    
    # 用于存标签投票统计的 JSON 字段，提高查询性能
    tag_votes_summary: Any = Field(sa_column=Column(JSON), default={})

    tags: List["Tag"] = Relationship(back_populates="threads", link_model=ThreadTagLink)
    votes: List["TagVote"] = Relationship(back_populates="thread")