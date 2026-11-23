"""统一身份在 Forum 的用户资料投影。"""

from datetime import datetime
from sqlmodel import SQLModel, Field
from shared.time_utils import utc_now


class User(SQLModel, table=True):
    """作品、书单和私人操作共用的账户；平台网关使用账号服务分配的 ID。"""

    __tablename__ = "user"

    id: str = Field(primary_key=True)
    """平台用户 ID，也是本站认定的作品作者和书单所有者 ID；旧 Fluxer 测试库保存 Fluxer ID。"""

    username: str
    """来源账号服务的用户名快照；仅收到绑定信息时可能为空。"""

    display_name: str | None = None
    """来源账号服务的公开显示名。"""

    avatar_url: str | None = None
    """来源账号服务的公开头像地址。"""

    updated_at: datetime = Field(default_factory=utc_now)
    """本地用户资料投影的更新时间。"""

    show_nsfw: bool = False
    """全局成人内容显示偏好。"""

    merged_into_id: str | None = None
    """管理合并后的最终账户，保留旧用户以便审计。"""
