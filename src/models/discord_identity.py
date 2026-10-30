"""权威绑定的只读本地投影。"""

from sqlmodel import SQLModel, Field


class DiscordIdentity(SQLModel, table=True):
    """只从 Fluxer 导入 Discord 绑定，不分配账户 ID。"""

    __tablename__ = "discord_identity"

    discord_user_id: str = Field(primary_key=True)
    """权威绑定中的 Discord 用户 ID，保留字符串形式。"""

    user_id: str = Field(unique=True, index=True)
    """该 Discord 账户绑定的 Fluxer 权威用户 ID。"""

    version: int = 1
    """Fluxer 权威绑定版本，用于拒绝旧事件覆盖。"""
