from typing import Literal

from pydantic import Field

from api.v1.schemas.tags.tag_response import TagResponse


class NativeTagSnapshotResponse(TagResponse):
    """目标上的只读 Discord 原生标签。"""

    source: Literal["discord"] = Field(description="来源固定为 discord")
    """来源固定为 discord"""

    readonly: Literal[True] = Field(description="固定为 true，原生标签在本项目不可修改")
    """固定为 true，原生标签在本项目不可修改"""
