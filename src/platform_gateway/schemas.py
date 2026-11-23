"""平台作品 HTTP 边界的请求类型。"""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
)


Title = Annotated[str, StringConstraints(max_length=60, strip_whitespace=True)]
Summary = Annotated[str, StringConstraints(max_length=200, strip_whitespace=True)]
Description = Annotated[str, StringConstraints(max_length=20000)]
Note = Annotated[str, StringConstraints(max_length=800)]
EntityId = Annotated[str, StringConstraints(min_length=1, max_length=200)]


class DraftInput(BaseModel):
    """新建草稿的可选元信息。"""

    model_config = ConfigDict(extra="forbid")
    title: Title = ""
    summary: Summary = ""


class WorkPatch(DraftInput):
    """平台可编辑的作品元信息。"""

    description: Description = ""
    layout: dict | None = None
    coverId: EntityId | None = None
    galleryIds: list[EntityId] = Field(default_factory=list, max_length=20)


class PublishAnswer(BaseModel):
    """发布答卷的一个字段。"""

    model_config = ConfigDict(extra="forbid")
    question: EntityId
    answer: StrictBool


class PublishInput(BaseModel):
    """发布承诺与答卷。"""

    model_config = ConfigDict(extra="forbid")
    acknowledgedTermsVersion: EntityId
    answers: list[PublishAnswer] = Field(max_length=50)


class DeleteInput(BaseModel):
    """删除时可记录原因。"""

    model_config = ConfigDict(extra="forbid")
    reason: Annotated[str, StringConstraints(strip_whitespace=True, max_length=500)] = (
        ""
    )


class ChatSettings(BaseModel):
    """更改或清除作品交流区标题。"""

    model_config = ConfigDict(extra="forbid")
    title: Title | None


class CollectionInput(BaseModel):
    """可编辑的书单属性。"""

    model_config = ConfigDict(extra="forbid")
    title: Title = ""
    description: Description = ""
    coverUploadId: EntityId | None = None
    isPublic: StrictBool = False
    isAnonymous: StrictBool = False
    defaultSort: Literal["order", "added", "updated"] = "order"
    tags: list[EntityId] = Field(default_factory=list, max_length=9)


class CollectionItemPatch(BaseModel):
    """书单各条目的点评和排序。"""

    model_config = ConfigDict(extra="forbid")
    note: Note = ""
    order: Annotated[StrictInt, Field(ge=-(2**31), lt=2**31)] = 0


class CollectionItemInput(CollectionItemPatch):
    """将一个当前可见的作品加入书单。"""

    workId: EntityId


class FollowViewedInput(BaseModel):
    """选择要标记已看的关注作品，省略时标记当前可见项。"""

    workIds: list[EntityId] | None = None
