from typing import Literal

from pydantic import BaseModel, Field

from api.v1.schemas.search import LatestUpdate, ThreadDetail
from shared.utc_datetime import UTCDateTime


class NotificationItem(BaseModel):
    """动态通知列表中的单项数据。"""

    id: int = Field(description="通知的数据库主键 ID")
    """通知的数据库主键 ID"""

    type: Literal["thread_update", "author_new_thread"] = Field(
        description="动态通知类型：作品更新或作者新作"
    )
    """动态通知类型：作品更新或作者新作"""

    thread: ThreadDetail = Field(description="通知关联作品的完整信息")
    """通知关联作品的完整信息"""

    update: LatestUpdate | None = Field(
        default=None,
        description="作品更新详情；作者新作通知固定为空",
    )
    """作品更新详情；作者新作通知固定为空"""

    created_at: UTCDateTime = Field(description="通知产生时间")
    """通知产生时间"""

    read_at: UTCDateTime | None = Field(
        default=None,
        description="用户阅读时间；为空表示未读",
    )
    """用户阅读时间；为空表示未读"""
