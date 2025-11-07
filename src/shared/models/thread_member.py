from typing import Optional
from sqlmodel import Field, SQLModel, BigInteger, Column, UniqueConstraint
from datetime import datetime, timezone

class ThreadMember(SQLModel, table=True):
    """存储帖子和成员的关联信息"""

    __tablename__ = "thread_member" # type: ignore
    __table_args__ = (
        UniqueConstraint("thread_id", "user_id", name="uk_thread_user_membership"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    thread_id: int = Field(
        sa_column=Column(BigInteger, index=True),
        description="帖子的 Discord ID "
    )

    user_id: int = Field(
        sa_column=Column(BigInteger, index=True),
        description="用户的 Discord ID "
    )

    joined_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
        description="成员加入帖子的时间 (UTC)"
    )