from pydantic import BaseModel, Field

class TagDetail(BaseModel):
    """用于 API 响应的标签详细信息"""
    id: int = Field(description="标签的 Discord ID")
    name: str = Field(description="标签的名称")