from pydantic import BaseModel, Field


class TagDetail(BaseModel):
    """标签的基础信息模型"""

    tag_id: int = Field(description="标签的 Discord ID")
    """标签的 Discord ID"""

    name: str = Field(description="标签名称")
    """标签名称"""
