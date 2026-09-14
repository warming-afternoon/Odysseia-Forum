import logging
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.v1.dependencies.security import get_current_user, require_auth
from api.v1.schemas.tags import TagStatsRequest, TagStatsResponse
from tag.tag_statistics_service import TagStatisticsService
from core.cache_service import CacheService

from api.v1.schemas.tags.tag_create_request import TagCreateRequest
from api.v1.schemas.tags.tag_update_request import TagUpdateRequest
from api.v1.schemas.tags.tag_aliases_request import TagAliasesRequest
from api.v1.schemas.tags.tag_relation_request import TagRelationRequest
from api.v1.schemas.tags.tag_proposal_request import TagProposalRequest
from api.v1.schemas.tags.tag_review_request import TagReviewRequest
from api.v1.schemas.tags.tag_selection_request import TagSelectionRequest
from api.v1.schemas.tags.tag_vote_request import TagVoteRequest
from dto.events.tag_command import TagCommand
from shared.enum.tag_category import TagCategory
from shared.tag_error import TagError
from shared.request_id import PositiveRequestId, RequestId

from shared.event_mediator import EventMediator

from api.v1.schemas.tags.tag_response import TagResponse
from api.v1.schemas.tags.tag_category_response import TagCategoryResponse
from api.v1.schemas.tags.tag_pool_item_response import TagPoolItemResponse
from api.v1.schemas.tags.tag_relation_response import TagRelationResponse
from api.v1.schemas.tags.tag_proposal_response import TagProposalResponse
from api.v1.schemas.tags.tag_audit_response import TagAuditResponse
from api.v1.schemas.tags.target_tags_response import TargetTagsResponse

logger = logging.getLogger(__name__)

# 全局依赖，将在 bot_main.py 中被注入
channel_mappings_config: Dict[int, List[Dict]] = {}
async_session_factory: async_sessionmaker | None = None
cache_service_instance: Optional[CacheService] = None
event_mediator: EventMediator | None = None
Target = Literal["thread", "booklist"]

router = APIRouter(prefix="/tags", tags=["标签"], dependencies=[Depends(require_auth)])


@router.post(
    "/stats",
    response_model=TagStatsResponse,
    summary="聚合查询标签统计数据",
)
async def stats_tags(
    request: TagStatsRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    一次性聚合获取指定范围内的所有标签使用统计情况
    """
    if not async_session_factory or not cache_service_instance:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="核心服务尚未初始化"
        )

    try:
        async with async_session_factory() as session:
            tag_service = TagStatisticsService(
                session=session,
                cache_service=cache_service_instance,
                channel_mappings=channel_mappings_config,
            )
            return await tag_service.aggregate_tag_stats(request)

    except Exception as e:
        logger.error(f"执行标签聚合查询时出错: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器内部错误，无法获取标签统计信息",
        )


async def dispatch(action: str, user: dict[str, Any], **payload: Any) -> Any:
    """通过请求事件调用标签领域并转换业务错误。"""
    mediator = event_mediator
    if mediator is None:
        raise HTTPException(503, "标签服务尚未初始化")
    try:
        return await mediator.request(TagCommand(action, int(user["id"]), payload))
    except TagError as exc:
        raise HTTPException(exc.status, exc.detail) from exc


@router.get("/categories", response_model=list[TagCategoryResponse])
async def categories(user=Depends(require_auth)):
    """返回稳定分类枚举。"""
    return [{"value": item.value, "name": item.name} for item in TagCategory]


@router.get("", response_model=list[TagPoolItemResponse])
async def pool(
    q: str = Query(default="", max_length=200),
    category: int | None = Query(default=None, ge=1, le=7),
    selectable: bool = True,
    include_deleted: bool = False,
    offset: int = Query(default=0, ge=0),
    user=Depends(require_auth),
):
    """按标准名或别名分页搜索标签池。"""
    return await dispatch(
        "pool",
        user,
        q=q,
        category=category,
        selectable=selectable,
        include_deleted=include_deleted,
        offset=offset,
    )


@router.get("/relations", response_model=list[TagRelationResponse])
async def relations(user=Depends(require_auth)):
    """提供全部直接关系边，层级由前端计算。"""
    return await dispatch("relations", user)


@router.post("", status_code=status.HTTP_201_CREATED, summary="创建标签", response_model=TagResponse)
async def create_tag(body: TagCreateRequest, user=Depends(require_auth)):
    """BOT 管理员创建一个标准标签。"""
    return await dispatch("manage", user, operation="create", **body.model_dump())


@router.patch("/{tag_id}", summary="修改标签", response_model=TagResponse)
async def update_tag(
    body: TagUpdateRequest, tag_id: PositiveRequestId, user=Depends(require_auth)
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
async def delete_tag(tag_id: PositiveRequestId, user=Depends(require_auth)):
    """软删除标签并保留治理历史。"""
    return await dispatch("manage", user, operation="delete", tag_id=tag_id)


@router.post("/{tag_id}/restore", summary="恢复标签", response_model=TagResponse)
async def restore_tag(tag_id: PositiveRequestId, user=Depends(require_auth)):
    """恢复标签实体，不恢复旧绑定。"""
    return await dispatch("manage", user, operation="restore", tag_id=tag_id)


@router.put("/{tag_id}/aliases", summary="替换标签别名", response_model=TagResponse)
async def replace_aliases(
    body: TagAliasesRequest, tag_id: PositiveRequestId, user=Depends(require_auth)
):
    """完整替换别名集合。"""
    return await dispatch(
        "manage", user, operation="update", tag_id=tag_id, **body.model_dump()
    )


@router.post("/{tag_id}/relations", summary="添加标签关系", response_model=TagResponse)
async def add_relation(
    body: TagRelationRequest, tag_id: PositiveRequestId, user=Depends(require_auth)
):
    """添加包含或互斥关系。"""
    return await dispatch(
        "manage", user, operation="add_relation", tag_id=tag_id, **body.model_dump()
    )


@router.delete("/{tag_id}/relations/{kind}/{target_tag_id}", summary="删除标签关系", response_model=TagResponse)
async def remove_relation(
    kind: Literal["implies", "excludes"],
    tag_id: PositiveRequestId,
    target_tag_id: PositiveRequestId,
    user=Depends(require_auth),
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


@router.get("/{target_type}/{target_id}", response_model=TargetTagsResponse)
async def read(target_type: Target, target_id: RequestId, user=Depends(require_auth)):
    """读取原生标签、自定义标签、票数和版本。"""
    return await dispatch("read", user, target_type=target_type, target_id=target_id)


@router.put("/{target_type}/{target_id}", response_model=TargetTagsResponse)
async def replace(
    target_type: Target,
    target_id: RequestId,
    body: TagSelectionRequest,
    user=Depends(require_auth),
):
    """作者或管理组替换完整自定义标签集合。"""
    return await dispatch(
        "replace",
        user,
        target_type=target_type,
        target_id=target_id,
        **body.model_dump(),
    )


@router.post("/{target_type}/{target_id}/proposals", response_model=TagProposalResponse)
async def propose(
    target_type: Target,
    target_id: RequestId,
    body: TagProposalRequest,
    user=Depends(require_auth),
):
    """提议一个标签，提交后开始七天审核期。"""
    return await dispatch(
        "propose",
        user,
        target_type=target_type,
        target_id=target_id,
        **body.model_dump(),
    )


@router.get("/{target_type}/{target_id}/proposals", response_model=list[TagProposalResponse])
async def proposals(
    target_type: Target,
    target_id: RequestId,
    review_queue: bool = False,
    offset: int = Query(default=0, ge=0),
    user=Depends(require_auth),
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


@router.put("/{target_type}/{target_id}/proposals/{proposal_id}", response_model=TagProposalResponse)
async def review(
    target_type: Target,
    target_id: RequestId,
    proposal_id: RequestId,
    body: TagReviewRequest,
    user=Depends(require_auth),
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


@router.put("/{target_type}/{target_id}/votes/{binding_id}", response_model=TargetTagsResponse)
async def vote(
    target_type: Target,
    target_id: RequestId,
    binding_id: RequestId,
    body: TagVoteRequest,
    user=Depends(require_auth),
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


@router.get("/{target_type}/{target_id}/audit", response_model=list[TagAuditResponse])
async def audit(
    target_type: Literal["thread", "booklist", "tag"],
    target_id: RequestId,
    offset: int = Query(default=0, ge=0),
    user=Depends(require_auth),
):
    """管理人员在授权范围内查询操作记录。"""
    return await dispatch(
        "audit", user, target_type=target_type, target_id=target_id, offset=offset
    )
