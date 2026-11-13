from datetime import datetime

from pydantic import BaseModel, Field


class ThreadMessageSnapshot(BaseModel):
    """承载消息解析结果，不携带数据库会话或频道快照。"""

    thread_id: int
    """Discord 帖子 ID，也是论坛帖首楼消息的 ID。"""

    author_id: int
    """解析后的作者 Discord ID，重建帖使用原始作者 ID。"""

    created_at: datetime
    """解析后的创建时间（UTC，无时区），重建帖优先使用原始创建时间。"""

    reaction_count: int
    """首楼当前最高单种反应数量。"""

    reactions: dict[str, int] = Field(default_factory=dict)
    """首帖完整逐表情计数，不包含子帖或推测的历史反应。"""

    reaction_emojis: dict[str, dict] = Field(default_factory=dict)
    """原生表情标识、名称与动画标志。"""

    first_message_excerpt: str
    """普通帖的首楼正文，或重建帖第二楼清理元数据后的摘要。"""

    thumbnail_urls: list[str]
    """从首楼或补档消息提取的图片 URL 列表，保留签名参数，确认无图时为空。"""
