"""全站标签池与书单标签的 V2 HTTP 协议适配。"""

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Response, status
from api.v2.dependencies import current_member, get_session_factory
from api.v1.schemas.tags.tag_create_request import TagCreateRequest
from api.v1.schemas.tags.tag_update_request import TagUpdateRequest
from api.v1.schemas.tags.tag_aliases_request import TagAliasesRequest
from api.v1.schemas.tags.tag_relation_request import TagRelationRequest
from api.v1.schemas.tags.tag_proposal_request import TagProposalRequest
from api.v1.schemas.tags.tag_review_request import TagReviewRequest
from api.v1.schemas.tags.tag_selection_request import TagSelectionRequest
from api.v1.schemas.tags.tag_vote_request import TagVoteRequest
from api.v1.schemas.tags.tag_merge_request import TagMergeRequest
from api.v1.schemas.tags.tag_merge_preview_response import TagMergePreviewResponse
from api.v1.schemas.tags.tag_response import TagResponse
from api.v1.schemas.tags.tag_category_response import TagCategoryResponse
from api.v1.schemas.tags.tag_pool_response import TagPoolResponse
from api.v1.schemas.tags.tag_relation_response import TagRelationResponse
from api.v1.schemas.tags.tag_proposal_response import TagProposalResponse
from api.v1.schemas.tags.tag_audit_response import TagAuditResponse
from api.v1.schemas.tags.target_tags_response import TargetTagsResponse
from dto.meta.user_role import UserRole
from dto.work.work_actor_dto import WorkActorDTO
from dto.work.work_command_qo import WorkCommandQo
from shared.enum.tag_category import TagCategory
from shared.request_id import PositiveRequestId, RequestId
from work.work_tag_management_service import WorkTagManagementService

Target = Literal["booklist"]
router = APIRouter(prefix="/v2/tag-management", tags=["标签管理 V2"])


async def dispatch(action: str, user: dict[str, Any], **payload: Any) -> Any:
    """将已验证身份与白名单路由操作传递到领域事务。"""
    return await WorkTagManagementService(get_session_factory()).dispatch(
        WorkCommandQo(
            actor=WorkActorDTO.from_principal(user), values=payload, operation=action
        )
    )


@router.get("/role", response_model=UserRole)
async def role(response: Response, user=Depends(current_member)):
    """返回界面能力，不替代写入接口的服务端授权。"""
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "is_management_member": bool(user["is_admin"]),
        "is_bot_admin": str(user["discord_user_id"]) in {
            str(value) for value in user["config"].get("bot_admin_user_ids", [])
        },
    }


@router.get(
    "/categories", summary="获取标签分类列表", response_model=list[TagCategoryResponse]
)
async def categories(user=Depends(current_member)):
    """返回稳定分类枚举。"""
    return [{"value": item.value, "name": item.name} for item in TagCategory]


@router.get("", summary="搜索标签池", response_model=TagPoolResponse)
async def pool(
    q: str = Query(
        default="",
        max_length=200,
        description="标签搜索关键词：对标准名或别名进行不区分大小写的包含匹配；留空则不按名称筛选，返回标准名",
    ),
    source: Literal["custom", "discord"] | None = Query(
        default=None,
        description="按标签实体当前来源筛选：custom=项目标签（含历史 Fluxer 标签与 DC 删除后转换的标签），discord=DC 原生标签；省略则返回两种来源，筛选在分页前生效",
    ),
    category: int | None = Query(
        default=None,
        ge=1,
        le=7,
        description="按分类筛选：1=癖好、2=作品、3=角色、4=特质、5=情节、6=背景、7=玩法；省略则包含所有分类及未分类标签",
    ),
    selectable: bool = Query(
        default=True,
        description="是否仅返回已启用标签；true 为仅已启用，false 同时包含停用标签。是否包含软删除记录由 include_deleted 独立控制；不检查具体帖子的容量或挂标权限",
    ),
    include_deleted: bool = Query(
        default=False,
        description="是否包含软删除标签；默认不包含，传 true 仅限 BOT 管理员，仍受 selectable 等筛选条件约束",
    ),
    user=Depends(current_member),
):
    """按标准名或别名搜索并返回完整标签池。"""
    results = await dispatch(
        "pool",
        user,
        q=q,
        source=source,
        category=category,
        selectable=selectable,
        include_deleted=include_deleted,
        _can_view_abyss=bool(user["can_view_abyss"]),
    )
    return {"results": results, "total": len(results)}


@router.get(
    "/relations", summary="获取标签关系列表", response_model=list[TagRelationResponse]
)
async def relations(user=Depends(current_member)):
    """提供全部直接关系边，层级由前端计算。"""
    return await dispatch(
        "relations",
        user,
        _can_view_abyss=bool(user["can_view_abyss"]),
    )


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="创建标签",
    response_model=TagResponse,
)
async def create_tag(body: TagCreateRequest, user=Depends(current_member)):
    """BOT 管理员创建一个标准标签。"""
    return await dispatch("manage", user, operation="create", **body.model_dump())


@router.get(
    "/{tag_id}/merge-preview",
    response_model=TagMergePreviewResponse,
    summary="预检标签合并",
)
async def merge_preview(
    tag_id: Annotated[PositiveRequestId, Path(description="待软删除的旧标签内部 ID")],
    target_tag_id: Annotated[
        PositiveRequestId, Query(description="合并后保留的标签内部 ID")
    ],
    user=Depends(current_member),
):
    """仅 BOT 管理员可预检，返回影响数量及冲突，不修改数据。"""
    return await dispatch(
        "merge", user, tag_id=tag_id, target_tag_id=target_tag_id, preview=True
    )


@router.post("/{tag_id}/merge", response_model=TagResponse, summary="合并同名标签")
async def merge_tags(
    tag_id: Annotated[
        PositiveRequestId,
        Path(description="合并后软删除的标签内部 ID，旧 ID 后续提示刷新"),
    ],
    body: TagMergeRequest,
    user=Depends(current_member),
):
    """仅 BOT 管理员可执行，重新检查版本及冲突，保留历史审计。"""
    return await dispatch("merge", user, tag_id=tag_id, **body.model_dump())


@router.patch("/{tag_id}", summary="修改标签", response_model=TagResponse)
async def update_tag(
    body: TagUpdateRequest,
    tag_id: Annotated[
        PositiveRequestId,
        Path(
            description="标签实体的内部 ID，非 Discord 标签 ID；接受十进制字符串或整数，必须大于 0"
        ),
    ],
    user=Depends(current_member),
):
    """原子修改名称、分类及启用状态。"""
    return await dispatch(
        "manage",
        user,
        operation="update",
        tag_id=tag_id,
        **body.model_dump(exclude_unset=True),
    )


@router.delete("/{tag_id}", summary="软删除标签", response_model=TagResponse)
async def delete_tag(
    tag_id: Annotated[
        PositiveRequestId,
        Path(
            description="标签实体的内部 ID，非 Discord 标签 ID；接受十进制字符串或整数，必须大于 0"
        ),
    ],
    user=Depends(current_member),
):
    """软删除标签并保留治理历史。"""
    return await dispatch("manage", user, operation="delete", tag_id=tag_id)


@router.post("/{tag_id}/restore", summary="恢复标签", response_model=TagResponse)
async def restore_tag(
    tag_id: Annotated[
        PositiveRequestId,
        Path(
            description="标签实体的内部 ID，非 Discord 标签 ID；接受十进制字符串或整数，必须大于 0"
        ),
    ],
    user=Depends(current_member),
):
    """恢复标签实体，不恢复旧绑定。"""
    return await dispatch("manage", user, operation="restore", tag_id=tag_id)


@router.put("/{tag_id}/aliases", summary="替换标签别名", response_model=TagResponse)
async def replace_aliases(
    body: TagAliasesRequest,
    tag_id: Annotated[
        PositiveRequestId,
        Path(
            description="标签实体的内部 ID，非 Discord 标签 ID；接受十进制字符串或整数，必须大于 0"
        ),
    ],
    user=Depends(current_member),
):
    """完整替换别名集合。"""
    return await dispatch(
        "manage", user, operation="update", tag_id=tag_id, **body.model_dump()
    )


@router.post("/{tag_id}/relations", summary="添加标签关系", response_model=TagResponse)
async def add_relation(
    body: TagRelationRequest,
    tag_id: Annotated[
        PositiveRequestId,
        Path(
            description="标签实体的内部 ID，非 Discord 标签 ID；接受十进制字符串或整数，必须大于 0"
        ),
    ],
    user=Depends(current_member),
):
    """添加包含或互斥关系。"""
    return await dispatch(
        "manage", user, operation="add_relation", tag_id=tag_id, **body.model_dump()
    )


@router.delete(
    "/{tag_id}/relations/{kind}/{target_tag_id}",
    summary="删除标签关系",
    response_model=TagResponse,
)
async def remove_relation(
    kind: Annotated[
        Literal["implies", "excludes"],
        Path(description="关系类型：implies=包含或触发关系，excludes=互斥关系"),
    ],
    tag_id: Annotated[
        PositiveRequestId,
        Path(
            description="标签实体的内部 ID，非 Discord 标签 ID；接受十进制字符串或整数，必须大于 0"
        ),
    ],
    target_tag_id: Annotated[
        PositiveRequestId,
        Path(
            description="关系另一端的标签内部 ID，非 Discord 标签 ID；implies 表示当前标签触发此标签，excludes 表示两者互斥；接受十进制字符串或整数，必须大于 0"
        ),
    ],
    user=Depends(current_member),
):
    """删除指定的直接标签关系。"""
    return await dispatch(
        "manage",
        user,
        operation="remove_relation",
        tag_id=tag_id,
        target_tag_id=target_tag_id,
        kind=kind,
    )


@router.get(
    "/{target_type}/{target_id}",
    summary="获取书单的标签",
    response_model=TargetTagsResponse,
)
async def read(
    target_type: Annotated[
        Target, Path(description="绑定目标类型：booklist=书单")
    ],
    target_id: Annotated[
        RequestId,
        Path(
            description="目标 ID：书单内部 ID；接受十进制字符串或整数"
        ),
    ],
    user=Depends(current_member),
):
    """读取原生标签、自定义标签、票数和版本。"""
    return await dispatch("read", user, target_type=target_type, target_id=target_id)


@router.put(
    "/{target_type}/{target_id}",
    summary="替换书单的本地标签",
    response_model=TargetTagsResponse,
)
async def replace(
    target_type: Annotated[
        Target, Path(description="绑定目标类型：booklist=书单")
    ],
    target_id: Annotated[
        RequestId,
        Path(
            description="目标 ID：书单内部 ID；接受十进制字符串或整数"
        ),
    ],
    body: TagSelectionRequest,
    user=Depends(current_member),
):
    """书单作者或管理组替换完整标签集合，允许绑定 DC 标签实体。"""
    return await dispatch(
        "replace",
        user,
        target_type=target_type,
        target_id=target_id,
        **body.model_dump(),
    )


@router.post(
    "/{target_type}/{target_id}/proposals",
    summary="提议添加标签",
    response_model=TagProposalResponse,
)
async def propose(
    target_type: Annotated[
        Target, Path(description="绑定目标类型：booklist=书单")
    ],
    target_id: Annotated[
        RequestId,
        Path(
            description="目标 ID：书单内部 ID；接受十进制字符串或整数"
        ),
    ],
    body: TagProposalRequest,
    user=Depends(current_member),
):
    """提议一个标签，提交后开始七天审核期。"""
    return await dispatch(
        "propose",
        user,
        target_type=target_type,
        target_id=target_id,
        **body.model_dump(),
    )


@router.get(
    "/{target_type}/{target_id}/proposals",
    summary="获取标签申请历史或审核队列",
    response_model=list[TagProposalResponse],
)
async def proposals(
    target_type: Annotated[
        Target, Path(description="绑定目标类型：booklist=书单")
    ],
    target_id: Annotated[
        RequestId,
        Path(
            description="目标 ID：书单内部 ID；接受十进制字符串或整数"
        ),
    ],
    review_queue: bool = Query(
        default=False,
        description="false 返回本人的标签申请历史（含各状态）；true 返回此目标的待审核申请，仅作者或有权限的管理人员可查",
    ),
    offset: int = Query(
        default=0,
        ge=0,
        description="分页跳过的记录数，不是页码；默认 0，每次最多返回 100 条，下一页可传 100、200 等",
    ),
    user=Depends(current_member),
):
    """读取本人申请或作者审核队列。"""
    return await dispatch(
        "proposals",
        user,
        target_type=target_type,
        target_id=target_id,
        review_queue=review_queue,
        offset=offset,
    )


@router.put(
    "/{target_type}/{target_id}/proposals/{proposal_id}",
    summary="审核标签添加申请",
    response_model=TagProposalResponse,
)
async def review(
    target_type: Annotated[
        Target, Path(description="绑定目标类型：booklist=书单")
    ],
    target_id: Annotated[
        RequestId,
        Path(
            description="目标 ID：书单内部 ID；接受十进制字符串或整数"
        ),
    ],
    proposal_id: Annotated[
        RequestId,
        Path(
            description="标签添加申请的内部 ID，取自申请接口响应；接受十进制字符串或整数"
        ),
    ],
    body: TagReviewRequest,
    user=Depends(current_member),
):
    """同意或拒绝待审核提议。"""
    return await dispatch(
        "review",
        user,
        target_type=target_type,
        target_id=target_id,
        proposal_id=proposal_id,
        **body.model_dump(),
    )


@router.put(
    "/{target_type}/{target_id}/votes/{binding_id}",
    summary="设置标签赞踩或撤票",
    response_model=TargetTagsResponse,
)
async def vote(
    target_type: Annotated[
        Target, Path(description="绑定目标类型：booklist=书单")
    ],
    target_id: Annotated[
        RequestId,
        Path(
            description="目标 ID：书单内部 ID；接受十进制字符串或整数"
        ),
    ],
    binding_id: Annotated[
        RequestId,
        Path(
            description="本轮标签绑定 ID，取自标签快照的 binding_id，不是标签 ID；接受十进制字符串或整数，已结束轮次不能投票"
        ),
    ],
    body: TagVoteRequest,
    user=Depends(current_member),
):
    """设置赞、踩或撤票，旧轮次不可继续投票。"""
    return await dispatch(
        "vote",
        user,
        target_type=target_type,
        target_id=target_id,
        binding_id=binding_id,
        **body.model_dump(),
    )


@router.get(
    "/{target_type}/{target_id}/audit",
    summary="查询标签操作记录",
    response_model=list[TagAuditResponse],
)
async def audit(
    target_type: Annotated[
        Literal["booklist", "tag"],
        Path(
            description="审计目标类型：booklist=书单，tag=标签池实体；标签池审计仅 BOT 管理员可查"
        ),
    ],
    target_id: Annotated[
        RequestId,
        Path(
            description="审计目标 ID：booklist 传内部书单 ID，tag 传内部标签 ID；接受十进制字符串或整数"
        ),
    ],
    offset: int = Query(
        default=0,
        ge=0,
        description="分页跳过的记录数，不是页码；默认 0，每次最多返回 100 条，下一页可传 100、200 等",
    ),
    user=Depends(current_member),
):
    """管理人员在授权范围内查询操作记录。"""
    return await dispatch(
        "audit", user, target_type=target_type, target_id=target_id, offset=offset
    )
