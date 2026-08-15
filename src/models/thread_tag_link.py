from typing import Optional

from sqlalchemy import BigInteger, Column, Index
from sqlmodel import Field, SQLModel


class ThreadTagLink(SQLModel, table=True):
    """帖子和标签的多对多关联表模型。"""

    __tablename__ = "thread_tag_link"  # type: ignore[assignment]
    __table_args__ = (
        Index("ix_thread_tag_link_tag_id_thread_id", "tag_id", "thread_id"),
    )

    thread_id: Optional[int] = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True),
    )
    tag_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    upvotes: int = Field(default=0)
    downvotes: int = Field(default=0)
