"""权威绑定的只读本地投影。"""

from sqlmodel import SQLModel, Field


class DiscordIdentity(SQLModel, table=True):
    """只从 Fluxer 导入 Discord 绑定，不分配账户 ID。"""

    __tablename__ = "discord_identity"

    discord_user_id: str = Field(primary_key=True)

    user_id: str = Field(unique=True, index=True)

    version: int = 1
