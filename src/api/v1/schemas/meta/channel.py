from pydantic import BaseModel, Field
from typing import List
from .tag_detail import TagDetail

class Channel(BaseModel):
    """用于 API 响应的频道及其包含的可用标签"""
    id: int = Field(description="频道的 Discord ID")
    name: str = Field(description="频道的名称")
    tags: List[TagDetail] = Field(description="该频道下所有可用的标签列表")