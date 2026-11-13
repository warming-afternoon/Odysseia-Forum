"""增长事件的有界收件去重记录。"""

from datetime import datetime
from sqlalchemy import Column, DateTime, JSON
from sqlmodel import SQLModel, Field


class WorkGrowthReceipt(SQLModel, table=True):
    """完整载荷短期保留，幂等键覆盖允许接受的重放窗口。"""

    __tablename__ = "work_growth_receipt"

    event_id: str = Field(primary_key=True)
    """源侧稳定事件编号。"""

    occurred_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    """源侧操作时间，存储为 UTC 无时区。"""

    received_at: datetime = Field(sa_column=Column(DateTime, nullable=False, index=True))
    """实际收件时间。"""

    payload: dict | None = Field(default=None, sa_column=Column(JSON, nullable=True))
    """短期诊断原文，清理后不承诺逐事件重建。"""

    outcome: str = "accepted"
    """正常、基线缺失或早于校准边界等可观测结果。"""
