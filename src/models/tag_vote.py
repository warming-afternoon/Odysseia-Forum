from typing import TYPE_CHECKING, Optional

from sqlalchemy import BigInteger, Column
from sqlmodel import Field, Relationship, SQLModel, UniqueConstraint

if TYPE_CHECKING:
    from models import Tag, Thread


class TagVote(SQLModel, table=True):
    """标签投票模型，记录用户对特定帖子中特定标签的评价。"""

    __tablename__ = "tag_vote"  # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "user_id", "tag_id", "thread_id", name="uq_user_tag_thread_vote"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(sa_column=Column(BigInteger, index=True))
    tag_id: int = Field(sa_column=Column(BigInteger, index=True))
    thread_id: int = Field(sa_column=Column(BigInteger, index=True))
    vote: int  # 1 代表赞成, -1 代表反对

    # 关系定义，用于 ORM 查询，不产生外键约束
    tag: "Tag" = Relationship(
        back_populates="votes",
        sa_relationship_kwargs={
            "primaryjoin": "TagVote.tag_id == Tag.id",
            "foreign_keys": "[TagVote.tag_id]",
        },
    )
    thread: "Thread" = Relationship(
        back_populates="votes",
        sa_relationship_kwargs={
            "primaryjoin": "TagVote.thread_id == Thread.id",
            "foreign_keys": "[TagVote.thread_id]",
        },
    )
