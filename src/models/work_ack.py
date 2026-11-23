"""用户对作品公告与规则的确认进度。"""

from sqlalchemy import BigInteger, CheckConstraint, Column
from sqlmodel import Field, SQLModel


class WorkAck(SQLModel, table=True):
    """按作品、平台用户和内容类型保存最后确认的序号。"""

    __tablename__ = "work_ack"
    __table_args__ = (
        CheckConstraint("kind IN ('announcement', 'rules')"),
        CheckConstraint("confirmed_version > 0"),
    )

    work_id: int = Field(sa_column=Column(BigInteger, primary_key=True))
    """已确认内容所属的作品编号。"""

    user_id: str = Field(primary_key=True)
    """实际阅读并确认的平台用户编号。"""

    kind: str = Field(primary_key=True)
    """区分公告序号和规则版本的确认进度。"""

    confirmed_version: int = Field(sa_column=Column(BigInteger, nullable=False))
    """已确认的最大作品内序号。"""
