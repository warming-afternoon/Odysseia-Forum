from pydantic import BaseModel, Field
from shared.request_id import PositiveRequestId


class TagProposalRequest(BaseModel):
    """单标签提议请求。"""

    tag_id: PositiveRequestId = Field(
        description="提议添加的自定义标签内部 ID，必须来自可用标签池"
    )
    """提议添加的自定义标签内部 ID"""
