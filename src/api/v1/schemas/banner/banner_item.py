"""Banner项目Schema"""
from pydantic import BaseModel


class BannerItem(BaseModel):
    """Banner轮播项"""

    thread_id: int
    title: str
    cover_image_url: str
    channel_id: int

    class Config:
        from_attributes = True