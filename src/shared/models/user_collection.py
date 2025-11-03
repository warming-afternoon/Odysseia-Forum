from typing import Optional
from sqlmodel import Field, SQLModel, BigInteger, Column, UniqueConstraint
from datetime import datetime, timezone


class UserCollection(SQLModel, table=True):
    """存储用户收藏的帖子"""

    __tablename__ = "user_collection"  # type: ignore
    __table_args__ = (
        UniqueConstraint("user_id", "thread_id", name="uk_user_thread_collection"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    user_id: int = Field(
        sa_column=Column(BigInteger, index=True), description="收藏用户的 Discord ID"
    )

    thread_id: int = Field(
        sa_column=Column(BigInteger, index=True), description="被收藏帖子的 Discord ID"
    )

    create_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        nullable=False,
        description="收藏时间 (UTC)",
    )
    note: Optional[str] = Field(default=None, description="用户为收藏添加的备注")
