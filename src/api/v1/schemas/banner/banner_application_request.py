"""Banner申请请求Schema"""

from pydantic import BaseModel, Field


class BannerApplicationRequest(BaseModel):
    """Banner申请请求模型"""

    thread_id: str = Field(
        ..., description="帖子ID（纯数字字符串）", min_length=17, max_length=20
    )
    cover_image_url: str = Field(..., description="封面图URL")
    target_scope: str = Field(..., description="展示范围：'global' 或频道ID")
