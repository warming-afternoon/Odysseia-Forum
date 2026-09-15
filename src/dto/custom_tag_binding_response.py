from typing import Literal

from pydantic import BaseModel, Field


class CustomTagBindingResponse(BaseModel):
    """帖子或书单当前生效的本地标签绑定信息。"""

    id: str = Field(description="标签内部 ID，以十进制字符串返回")
    """标签内部 ID"""

    name: str = Field(description="标签标准名，不含分类前缀")
    """标签标准名"""

    category: int | None = Field(
        description="分类枚举：1=癖好，2=作品，3=角色，4=特质，5=情节，6=背景，7=玩法"
    )
    """标签分类整数值"""

    category_name: str | None = Field(description="分类枚举对应的中文名称")
    """标签分类名称"""

    source: Literal["discord", "custom"] = Field(
        description="标签实体来源，与本地绑定来源独立"
    )
    """标签实体来源，与本地绑定独立"""

    enabled: bool = Field(description="标签是否启用；停用标签的现有绑定仍可展示")
    """标签池中的启用状态"""

    binding_id: str = Field(
        description="当前绑定轮次 ID，以十进制字符串返回；重新添加产生新轮次"
    )
    """当前标签绑定轮次 ID"""

    upvotes: int = Field(description="当前绑定轮次的正向票数")
    """当前轮次正向票数"""

    downvotes: int = Field(description="当前绑定轮次的负向票数")
    """当前轮次负向票数"""
