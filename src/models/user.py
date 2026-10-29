"""Fluxer 用户资料在 Forum 的本地投影。"""

from datetime import datetime
from sqlmodel import SQLModel, Field
from shared.time_utils import utc_now


class User(SQLModel, table=True):
    """统一账户与作者；id 始终是 Fluxer 权威 ID。"""

    __tablename__ = "user"

    id: str = Field(primary_key=True)

    username: str

    display_name: str | None = None

    avatar_url: str | None = None

    updated_at: datetime = Field(default_factory=utc_now)
