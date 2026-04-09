from typing import List, Optional
from pydantic import BaseModel, Field

class TagStatsRequest(BaseModel):
    """获取标签聚合统计信息的请求体"""

    guild_id: Optional[int] = Field(default=None, description="服务器的 Discord ID")
    """服务器的 Discord ID"""

    channel_ids: Optional[List[int]] = Field(default=None, description="指定频道 ID 列表")
    """指定频道 ID 列表"""

    include_virtual: bool = Field(default=True, description="是否包含虚拟映射标签的统计")
    """是否包含虚拟映射标签的统计"""