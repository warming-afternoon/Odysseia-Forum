from typing import Self

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator


class TagUpdateRequest(BaseModel):
    """部分更新名称、分类或启用状态，禁止空更新与显式空值。"""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(
        default=None,
        min_length=1,
        max_length=100,
        description="新的标签标准名，不含分类前缀；未传保持原值，不允许显式 null",
    )
    """新的标签标准名；未传保持原值，不允许显式 null"""

    category: int | None = Field(
        default=None,
        ge=1,
        le=7,
        description="分类枚举：1=癖好，2=作品，3=角色，4=特质，5=情节，6=背景，7=玩法；未传保持原值，不允许显式 null",
    )
    """新的分类整数值；未传保持原值，不允许显式 null"""

    enabled: StrictBool | None = Field(
        default=None,
        description="true=启用，false=停用；未传保持原值，不允许显式 null；恢复软删除标签需使用恢复接口",
    )
    """启用或停用标签；未传保持原值，不可替代软删除恢复操作"""

    @model_validator(mode="after")
    def validate_changes(self) -> Self:
        """未传字段保持原值，传入字段必须具有有效值。"""
        if not self.model_fields_set or any(
            getattr(self, key) is None for key in self.model_fields_set
        ):
            raise ValueError("至少提供一个修改字段，字段不能为 null")
        return self
