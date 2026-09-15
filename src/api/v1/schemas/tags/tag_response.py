from typing import Literal

from pydantic import BaseModel, Field

from shared.utc_datetime import UTCDateTime


class TagResponse(BaseModel):
    """标签实体的公开基本信息。"""

    id: str = Field(description="标签内部 ID，以十进制字符串返回")
    """标签内部 ID，以十进制字符串返回"""

    name: str = Field(description="标准名，不含分类前缀")
    """标准名，不含分类前缀"""

    source: Literal["discord", "custom"] = Field(
        description="来源：discord 为原生标签，custom 为自定义标签"
    )
    """来源：discord 为原生标签，custom 为自定义标签"""

    discord_tag_id: str | None = Field(
        description="Discord 原生标签 ID，以字符串返回；新建自定义标签为空，DC 转换标签保留原始 ID"
    )
    """Discord 原生标签 ID，以字符串返回；新建自定义标签为空，DC 转换标签保留原始 ID"""

    category: int | None = Field(description="分类枚举值 1–7；原生及未分类转换标签为空")
    """分类枚举值 1–7；原生及未分类转换标签为空"""

    category_name: str | None = Field(
        description="分类中文名；原生及未分类转换标签为空"
    )
    """分类中文名；原生及未分类转换标签为空"""

    enabled: bool = Field(description="标签是否启用")
    """标签是否启用"""

    deleted_at: UTCDateTime | None = Field(
        description="软删除时间（UTC）；未删除时为空"
    )
    """软删除时间（UTC）；未删除时为空"""
