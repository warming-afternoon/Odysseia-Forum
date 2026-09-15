from typing import Annotated

from pydantic import BaseModel, Field

from api.v1.schemas.tags.custom_tag_snapshot_response import CustomTagSnapshotResponse
from api.v1.schemas.tags.native_tag_snapshot_response import NativeTagSnapshotResponse


class TargetTagsResponse(BaseModel):
    """目标当前标签集合及并发校验版本。"""

    version: str = Field(
        description="标签集合版本标记；完整替换时原样传回，过期返回 409"
    )
    """用于避免覆盖并发修改的版本标记"""

    tags: list[
        Annotated[
            NativeTagSnapshotResponse | CustomTagSnapshotResponse,
            Field(discriminator="binding_source"),
        ]
    ] = Field(
        description="当前生效的原生和自定义标签；按 binding_source 区分结构，无标签时为空数组"
    )
    """原生标签只读；自定义标签包含当前轮次票数及本人投票"""
