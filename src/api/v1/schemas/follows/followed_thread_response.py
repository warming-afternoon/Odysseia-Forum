"""关注列表中单个帖子的响应模型 — 继承 ThreadDetail 追加关注专用字段。"""

from datetime import datetime
from typing import Optional

from pydantic import Field

from api.v1.schemas.search.thread_detail import ThreadDetail


class FollowedThreadResponse(ThreadDetail):
    """关注列表中的帖子响应。

    继承 ThreadDetail 的所有字段（thread_id, title, author, tags 等），
    追加 latest_update_at / latest_update_link / followed_at / last_viewed_at / has_update。
    """

    latest_update_at: Optional[datetime] = Field(
        default=None, description="帖子最近一次有新消息的时间"
    )

    latest_update_link: Optional[str] = Field(
        default=None, description="最新更新的消息链接"
    )

    followed_at: datetime = Field(description="用户关注该帖子的时间")

    last_viewed_at: Optional[datetime] = Field(
        default=None, description="用户最近一次查看该帖子的时间"
    )

    has_update: bool = Field(
        default=False,
        description="自 last_viewed_at 后帖子是否有新内容",
    )
