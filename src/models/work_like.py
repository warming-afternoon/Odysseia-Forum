"""作品的平台点赞明细。"""

from datetime import datetime

from sqlalchemy import BigInteger, Column, text
from sqlmodel import Field, SQLModel

from shared.time_utils import utc_now


class WorkLike(SQLModel, table=True):
    """一个平台用户对一部作品当前生效的点赞。"""

    __tablename__ = "work_like"

    work_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    """接受点赞的作品编号。"""

    user_id: str = Field(primary_key=True)
    """点击点赞的平台用户编号。"""

    created_at: datetime = Field(default_factory=utc_now, sa_column_kwargs={"server_default": text("timezone('UTC', now())")})
    """用户最近建立本条点赞关系的时间。"""
