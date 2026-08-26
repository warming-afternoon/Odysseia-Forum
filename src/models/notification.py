from datetime import datetime
from typing import Optional

from sqlalchemy import BigInteger, CheckConstraint, Column, Index, String, UniqueConstraint, text
from sqlmodel import Field, SQLModel

from shared.time_utils import utc_now


class Notification(SQLModel, table=True):
    """存储面向单个接收者的动态通知。"""

    __tablename__ = "notification"  # type: ignore
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "event_type",
            "event_source_id",
            name="uk_notification_user_event_source",
        ),
        CheckConstraint(
            "event_type IN ('thread_update', 'author_new_thread')",
            name="ck_notification_event_type",
        ),
        Index(
            "ix_notification_user_created",
            "user_id",
            "created_at",
            "id",
        ),
        Index(
            "ix_notification_event_source",
            "event_type",
            "event_source_id",
        ),
        Index(
            "ix_notification_unread_user",
            "user_id",
            "created_at",
            postgresql_where=text("read_at IS NULL"),
        ),
        Index(
            "ix_notification_unread_user_thread",
            "user_id",
            "thread_id",
            postgresql_where=text("read_at IS NULL"),
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(
        sa_column=Column(BigInteger, nullable=False),
        description="通知接收者 Discord ID",
    )
    event_type: str = Field(
        sa_column=Column(String(32), nullable=False),
        description="通知事件类型",
    )
    event_source_id: int = Field(
        sa_column=Column(BigInteger, nullable=False),
        description="事件来源逻辑 ID",
    )
    thread_id: int = Field(
        sa_column=Column(BigInteger, nullable=False),
        description="关联作品 Discord Thread ID",
    )
    created_at: datetime = Field(
        default_factory=utc_now,
        nullable=False,
        description="通知创建时间",
    )
    read_at: Optional[datetime] = Field(
        default=None,
        nullable=True,
        description="通知阅读时间",
    )
