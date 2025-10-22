from pydantic import BaseModel, Field
from typing import Optional


class AuthorDetail(BaseModel):
    """
    API 响应中作者的详细信息模型
    """

    id: int = Field(description="作者的 Discord 用户 ID")
    name: str = Field(description="作者的唯一用户名")
    global_name: Optional[str] = Field(description="作者的全局显示名称")
    display_name: str = Field(description="作者的服务器内显示名称")
    avatar_url: Optional[str] = Field(description="作者头像的 URL")

    class Config:
        from_attributes = True
