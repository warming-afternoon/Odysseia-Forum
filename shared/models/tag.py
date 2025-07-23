from typing import List, Optional, TYPE_CHECKING
from sqlmodel import Field, SQLModel, Relationship

from .thread_tag_link import ThreadTagLink

if TYPE_CHECKING:
    from .thread import Thread
    from .tag_vote import TagVote

class Tag(SQLModel, table=True):
    """标签模型。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    
    threads: List["Thread"] = Relationship(back_populates="tags", link_model=ThreadTagLink)
    votes: List["TagVote"] = Relationship(back_populates="tag")