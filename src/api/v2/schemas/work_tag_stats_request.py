"""作品标签统计范围。"""

from typing import Literal
from pydantic import BaseModel, Field, field_validator


class WorkTagStatsRequest(BaseModel):
    """使用主来源平台和频道限定可见作品，编号按字符串传输。"""

    platform: Literal["discord", "fluxer"] | None = None
    """主来源平台；仅传服务器或频道时默认使用 Discord 命名空间。"""

    guild_id: str | None = None
    """主来源服务器编号。"""

    channel_ids: list[str] | None = Field(default=None, max_length=1000)
    """主来源频道编号，空列表表示不限定频道。"""

    include_virtual: bool = True
    """是否包含现有 Discord 频道映射形成的虚拟标签。"""

    @field_validator("guild_id", "channel_ids", mode="before")
    @classmethod
    def normalize_ids(cls, value):
        """兼容整数编号，但拒绝布尔值和非十进制的平台编号。"""
        if value is None:
            return None
        values = value if isinstance(value, list) else [value]
        normalized = []
        for item in values:
            if isinstance(item, bool) or not isinstance(item, (str, int)):
                raise ValueError("服务器和频道编号必须是十进制整数")
            text = str(item)
            if not text.isascii() or not text.isdecimal() or int(text) <= 0:
                raise ValueError("服务器和频道编号必须是正整数")
            normalized.append(str(int(text)))
        return normalized if isinstance(value, list) else normalized[0]
