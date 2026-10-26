"""频道模型"""

from datetime import datetime

from sqlalchemy import BigInteger, Column
from sqlmodel import Field, SQLModel

from shared.time_utils import utc_now


class Channel(SQLModel, table=True):
    """频道模型（文字频道、后续可扩展论坛频道等）。"""

    __tablename__ = "channel"

    id: int | None = Field(default=None, primary_key=True)
    """数据库主键 ID"""

    channel_id: int = Field(
        sa_column=Column(BigInteger, unique=True, index=True, nullable=False),
        description="Discord 频道 ID",
    )
    """Discord 频道 ID"""

    guild_id: int = Field(
        sa_column=Column(BigInteger, index=True, nullable=False),
        description="所属 Discord 服务器 ID",
    )
    """所属 Discord 服务器 ID"""

    guild_name: str | None = Field(default=None, description="服务器名称")
    """服务器名称，历史记录待 BOT 补齐"""

    name: str = Field(description="频道名称")
    """频道名称"""

    topic: str | None = Field(default=None, description="频道简介/描述")
    """频道简介/描述"""

    category_id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, nullable=True),
        description="所属频道分组 ID",
    )
    """所属频道分组 ID"""

    category_name: str | None = Field(default=None, description="频道分组名称")
    """频道分组名称，无分组或尚未同步时为空"""

    channel_type: int | None = Field(default=None, description="Discord 原生频道类型")
    """Discord 原生类型整数，论坛频道为 15，文字频道为 0"""

    synced_at: datetime | None = Field(default=None, description="最近同步时间（UTC）")
    """最近成功采集频道资料的时间，历史记录待 BOT 补齐"""

    created_at: datetime = Field(
        default_factory=utc_now, description="频道在 Discord 中的创建时间"
    )
    """频道在 Discord 中的创建时间"""
