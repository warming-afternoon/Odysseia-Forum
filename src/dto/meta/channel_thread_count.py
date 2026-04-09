from pydantic import BaseModel, Field


class ChannelThreadCount(BaseModel):
    """频道帖子统计聚合结果"""

    channel_id: int = Field(description="频道 ID")
    """频道 ID"""

    thread_count: int = Field(description="有效帖子总数")
    """有效帖子总数"""