from typing import List
from pydantic import BaseModel, Field
from api.v1.schemas.tags.channel_tag_info import ChannelTagInfo

class TagStatItem(BaseModel):
    """标签维度的聚合统计项"""

    tag_name: str = Field(description="标签名称")
    """标签名称"""

    total_thread_count: int = Field(description="该标签下的总帖子数(跨频道累加)")
    """该标签下的总帖子数(跨频道累加)"""

    channel_info: List[ChannelTagInfo] = Field(description="按频道分桶的详细统计数据")
    """按频道分桶的详细统计数据"""