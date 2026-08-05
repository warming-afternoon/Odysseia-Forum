from pydantic import BaseModel, Field


class BooklistCoverCandidateDTO(BaseModel):
    """书单 OG 封面候选。"""

    thread_id: int = Field(description="提供候选封面的 Discord 帖子 ID")
    image_url: str = Field(description="帖子中按提取顺序选中的首张合法图片 URL")
