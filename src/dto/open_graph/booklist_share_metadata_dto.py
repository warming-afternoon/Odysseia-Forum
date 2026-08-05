"""书单动态 Open Graph 响应的数据传输对象。"""

from datetime import datetime

from pydantic import BaseModel, Field


class BooklistShareMetadataDTO(BaseModel):
    """对外分享所需的最小书单元数据。"""

    title: str = Field(description="书单标题")
    description: str | None = Field(default=None, description="书单简介")
    image_url: str | None = Field(
        default=None,
        description="当前可用于 OG 的封面 URL；为空时调用方应使用站点默认图",
    )
    item_count: int = Field(description="书单内帖子数量")
    updated_at: datetime = Field(description="书单最后更新时间")
