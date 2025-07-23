from typing import List, Optional, TYPE_CHECKING
from sqlmodel import Field, SQLModel, Relationship
from datetime import datetime

if TYPE_CHECKING:
    from .tag import Tag
    from .thread_tag_link import ThreadTagLink

class Thread(SQLModel, table=True):
    """帖子模型。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    channel_id: int = Field(index=True)
    thread_id: int = Field(index=True, unique=True)
    title: str
    author_id: int
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    
    tags: List["Tag"] = Relationship(back_populates="threads", link_model=ThreadTagLink)