"""作品推荐位申请的输入边界。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator
from urllib.parse import urlsplit


class WorkBannerApplicationRequest(BaseModel):
    """只接受封面和展示范围，不接受申请人或审核状态。"""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    cover_image_url: str | None = Field(default=None, max_length=2048)
    """可选自定义封面，留空使用作品首图。"""

    target_scope: str = Field(default="global", max_length=19)
    """全局或明确的展示频道编号。"""

    @field_validator("cover_image_url")
    @classmethod
    def valid_cover(cls, value):
        """只保存可用 HTTP 地址，不在申请接口抓取外部图片。"""
        if not value:
            return None
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("封面必须为HTTP图片链接")
        return value

    @field_validator("target_scope")
    @classmethod
    def valid_scope(cls, value):
        """范围编号必须可安全存入数据库。"""
        if value == "global":
            return value
        if not value.isascii() or not value.isdigit() or not 0 < int(value) < 2**63:
            raise ValueError("展示范围无效")
        return value
