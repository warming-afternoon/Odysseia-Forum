from typing import TYPE_CHECKING, List

from sqlalchemy import BigInteger
from sqlmodel import Column, Field, Relationship, SQLModel

from models import ThreadTagLink

if TYPE_CHECKING:
    from models import TagVote, Thread


class Tag(SQLModel, table=True):
    """标签模型。"""

    id: int = Field(
        sa_column=Column(BigInteger, primary_key=True),
        description="Discord tag的id",
    )
    name: str = Field(index=True)

    threads: List["Thread"] = Relationship(
        back_populates="tags", link_model=ThreadTagLink
    )
    votes: List["TagVote"] = Relationship(back_populates="tag")
