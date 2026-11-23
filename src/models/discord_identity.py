"""权威绑定的只读本地投影。"""

from sqlalchemy import Index, text
from sqlmodel import SQLModel, Field


class DiscordIdentity(SQLModel, table=True):
    """记录账号服务确认的 Discord 当前与历史绑定，用于来源资料归属。"""

    __tablename__ = "discord_identity"

    __table_args__ = (
        Index(
            "uq_discord_identity_current_user",
            "user_id",
            unique=True,
            postgresql_where=text("is_current"),
        ),
    )

    is_current: bool = Field(default=True, index=True)
    """标记当前绑定；历史绑定仍可用于认领来源作品和书单。"""

    discord_user_id: str = Field(primary_key=True)
    """权威绑定中的 Discord 用户 ID，保留字符串形式。"""

    user_id: str = Field(index=True)
    """该 Discord 来源账号归属的平台用户 ID，逻辑关联 user.id。"""

    version: int = 1
    """该用户最近应用的权威绑定版本，用于审计乱序和重复投递。"""
