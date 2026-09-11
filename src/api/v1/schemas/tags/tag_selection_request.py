from pydantic import BaseModel, Field


class TagSelectionRequest(BaseModel):
    """作者提交的完整自定义标签集合。"""

    version: str = Field(
        description="读取目标标签时返回的版本标记，提交时原样传回；用于防止覆盖并发修改，过期返回 409 stale_version"
    )
    """GET 返回的标签集合版本标记，原样传回用于并发校验"""

    tag_ids: list[int] = Field(
        max_length=100,
        description="目标完整自定义标签内部 ID 集合，不含原生标签；未传入的现有自定义标签将解绑，空列表表示全部解绑",
    )
    """目标完整自定义标签 ID 集合；不含原生标签，空列表表示清空自定义标签"""
