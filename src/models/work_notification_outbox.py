"""V2 通知变化的事务性待投递元数据。"""
from datetime import datetime
from sqlalchemy import BigInteger, Column, DateTime, Index, Integer, String, text
from sqlmodel import Field, SQLModel


class WorkNotificationOutbox(SQLModel, table=True):
    __tablename__ = "work_notification_outbox"
    __table_args__ = (Index("ix_work_notification_outbox_pending", "id", postgresql_where=text("published_at IS NULL")),)

    id: int | None = Field(default=None, sa_column=Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True))
    recipient_id: str = Field(sa_column=Column(String(128), nullable=False))
    created_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")))
    published_at: datetime | None = Field(default=None, sa_column=Column(DateTime(timezone=True)))
    attempts: int = Field(default=0, sa_column=Column(Integer, nullable=False, server_default=text("0")))
