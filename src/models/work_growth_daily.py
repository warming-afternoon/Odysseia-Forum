"""作品按 UTC 日期累计的净增长。"""

from datetime import date
from sqlalchemy import BigInteger, Column, Date, String
from sqlmodel import SQLModel, Field


class WorkGrowthDaily(SQLModel, table=True):
    """保存允许负值的日增量，不将历史总量当成当天增长。"""

    __tablename__ = "work_growth_daily"

    work_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    """所属逻辑作品。"""

    day: date = Field(sa_column=Column(Date, primary_key=True))
    """实际发生时间对应的 UTC 日期。"""

    metric: str = Field(sa_column=Column(String(16), primary_key=True))
    """点赞、讨论或收藏指标。"""

    delta: int = Field(default=0, sa_column=Column(BigInteger, nullable=False))
    """新增减去取消及删除后的净变化。"""
