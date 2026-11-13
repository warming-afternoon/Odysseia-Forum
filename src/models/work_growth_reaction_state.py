"""增长计算专用表情状态，与权威展示列表隔离。"""

from datetime import datetime
from sqlalchemy import Column, DateTime, JSON
from sqlmodel import SQLModel, Field


class WorkGrowthReactionState(SQLModel, table=True):
    """允许暂时负值，避免乱序取消时截零永久放大计数。"""

    __tablename__ = "work_growth_reaction_state"

    platform: str = Field(primary_key=True)
    """来源平台。"""

    thread_id: str = Field(primary_key=True)
    """主讨论帖标识。"""

    counts: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    """逐表情增长基线，不能直接作为用户可见反应列表。"""

    cutoff_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    """最近基线采样时刻，更早迟到事件仅记缺口不再叠加。"""

    snapshot_counts: dict = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    """最近权威快照，仅用于对比和静默校准。"""

    snapshot_at: datetime = Field(sa_column=Column(DateTime, nullable=False))
    """权威快照采样时间。"""

    last_event_received_at: datetime | None = Field(default=None)
    """最近一次有效活动的本机收件时间。"""

    last_event_at: datetime | None = Field(default=None)
    """已处理活动的最大源侧时间。"""
