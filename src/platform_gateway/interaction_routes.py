"""平台作品点赞、收藏和关注接口。"""

from fastapi import APIRouter, Depends, Request, Response

from api.v2.dependencies import get_session_factory
from platform_gateway.http import PlatformRoute
from platform_gateway.principal import gateway_principal
from platform_gateway.schemas import FollowViewedInput
from platform_gateway.work_api_service import WorkApiService
from platform_gateway.work_interaction_service import WorkInteractionService


router = APIRouter(route_class=PlatformRoute)


async def interaction_service(principal=Depends(gateway_principal)):
    """为作品互动分配请求会话。"""
    async with get_session_factory()() as session:
        yield WorkInteractionService(WorkApiService(session, principal))


@router.post("/works/{id}/follow", status_code=204)
@router.delete("/works/{id}/follow", status_code=204)
async def set_follow(id: str, request: Request, service=Depends(interaction_service)):
    """手动关注或取关作品。"""
    await service.follow(id, follow=request.method == "POST")
    return Response(status_code=204)


@router.post("/works/{id}/like", status_code=204)
@router.delete("/works/{id}/like", status_code=204)
async def set_like(id: str, request: Request, service=Depends(interaction_service)):
    """点赞或取消点赞。"""
    await service.like(id, like=request.method == "POST")
    return Response(status_code=204)


@router.post("/works/{id}/favorite", status_code=204)
@router.delete("/works/{id}/favorite", status_code=204)
async def set_favorite(id: str, request: Request, service=Depends(interaction_service)):
    """加入默认收藏或从默认收藏移除。"""
    await service.favorite(id, favorite=request.method == "POST")
    return Response(status_code=204)


@router.get("/me/follows")
async def list_follows(
    request: Request, onlyUpdated: bool = False, service=Depends(interaction_service)
):
    """分页列出本人关注的当前可见作品。"""
    return await service.follows(request.query_params, only_updated=onlyUpdated)


@router.post("/me/follows/mark-viewed", status_code=204)
async def mark_follows_viewed(
    body: FollowViewedInput = FollowViewedInput(), service=Depends(interaction_service)
):
    """标记指定或当前所有可见的关注作品已看。"""
    await service.mark_viewed(body.workIds)
    return Response(status_code=204)
