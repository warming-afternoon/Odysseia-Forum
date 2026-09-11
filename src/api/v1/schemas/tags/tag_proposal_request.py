from pydantic import BaseModel, Field


class TagProposalRequest(BaseModel):
    """单标签提议请求。"""

    tag_id: int = Field(
        gt=0, description="提议添加的自定义标签内部 ID，必须来自可用标签池"
    )
    """提议添加的自定义标签内部 ID"""
