from typing import List, Optional, TYPE_CHECKING
from sqlmodel import Field, SQLModel, Relationship

if TYPE_CHECKING:
    from .thread import Thread
    from .thread_tag_link import ThreadTagLink

class Tag(SQLModel, table=True):
    """标签模型。"""
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    
    threads: List["Thread"] = Relationship(back_populates="tags", link_model=ThreadTagLink)