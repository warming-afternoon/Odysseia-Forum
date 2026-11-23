"""平台公开协议路由及聊天查验入口。"""

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select

from api.v2.dependencies import get_session_factory
from models.thread import Thread
from models.work import Work
from platform_gateway.collection_api_service import CollectionApiService
from platform_gateway.http import PlatformRoute
from platform_gateway.interaction_routes import router as interaction_router
from platform_gateway.policy import chat_policy, valid_bigint
from platform_gateway.principal import gateway_principal, verify_internal_token
from platform_gateway.schemas import (
    ChatSettings,
    CollectionInput,
    CollectionItemInput,
    CollectionItemPatch,
    DeleteInput,
    DraftInput,
    PublishInput,
    WorkPatch,
)
from platform_gateway.work_api_service import WorkApiService
from platform_gateway.search_suggestions import search_suggestions
from platform_gateway.work_trash_service import WorkTrashService, trash_work_id
from platform_gateway.work_notice_routes import router as work_notice_router


router = APIRouter(prefix="/api/v1", route_class=PlatformRoute)
router.include_router(interaction_router)
router.include_router(work_notice_router)
internal = APIRouter(route_class=PlatformRoute, include_in_schema=False)


async def work_service(principal=Depends(gateway_principal)):
    """共享既有会话工厂，为请求分配作品服务。"""
    async with get_session_factory()() as session:
        yield WorkApiService(session, principal)


async def collection_service(principal=Depends(gateway_principal)):
    """共享既有会话工厂，为请求分配书单服务。"""
    async with get_session_factory()() as session:
        yield CollectionApiService(session, principal)


@router.get("/works")
async def list_works(request: Request, service=Depends(work_service)):
    """列出当前模式允许访问的作品。"""
    return await service.list(request.query_params)


@router.post("/works", status_code=201)
async def create_work(body: DraftInput = DraftInput(), service=Depends(work_service)):
    """创建仅对作者可见的草稿。"""
    return await service.create(body)


@router.get("/works/by-legacy/discord/{threadId}")
async def lookup_discord_work(threadId: str, service=Depends(work_service)):
    """将已索引的 Discord 帖子映射到当前可见的作品。"""
    if not valid_bigint(threadId):
        raise HTTPException(404, "资源不存在")
    work_id = await service.session.scalar(
        select(Thread.work_id).where(
            Thread.thread_id == int(threadId), Thread.platform == "discord"
        )
    )
    if work_id is None:
        raise HTTPException(404, "资源不存在")
    await service.visible(str(work_id))
    return {"workId": str(work_id)}


@router.get("/works/{id}")
async def get_work(id: str, service=Depends(work_service)):
    """取作品详情或作者自己的草稿。"""
    row = await service.visible(id, own_draft=True)
    return (await service.repository.project([row], service.principal))[0]


@router.patch("/works/{id}")
async def patch_work(id: str, body: WorkPatch, service=Depends(work_service)):
    """更新平台作品资料。"""
    return await service.patch(id, body)


@router.delete("/works/{id}", status_code=204)
async def delete_work(
    id: str, body: DeleteInput = DeleteInput(), service=Depends(work_service)
):
    """保留记录并从普通查询隐藏。"""
    await service.delete(id, body)
    return Response(status_code=204)


@router.post("/works/{id}/publish")
async def publish_work(id: str, body: PublishInput, service=Depends(work_service)):
    """发布作者草稿。"""
    return await service.publish(id, body)


@router.post("/works/{id}/restore")
async def restore_work(
    id: str, body: DeleteInput = DeleteInput(), service=Depends(work_service)
):
    """按回收站规则恢复被软删的作品。"""
    return await WorkTrashService(service.session, service.principal).restore(id, body)


@router.get("/admin/trash/works")
async def list_work_trash(request: Request, service=Depends(work_service)):
    """列出当前站务能查看的作品回收站条目。"""
    return await WorkTrashService(service.session, service.principal).list(
        request.query_params
    )


@router.post("/admin/trash/works/{itemId}/restore")
async def restore_trash_work(
    itemId: str, body: DeleteInput = DeleteInput(), service=Depends(work_service)
):
    """恢复指定回收站条目。"""
    return await WorkTrashService(service.session, service.principal).restore(
        trash_work_id(itemId), body, admin=True
    )


@router.delete("/admin/trash/works/{itemId}", status_code=204)
async def purge_trash_work(
    itemId: str, body: DeleteInput = DeleteInput(), service=Depends(work_service)
):
    """超管永久清除指定回收站条目。"""
    await WorkTrashService(service.session, service.principal).purge(
        trash_work_id(itemId), body
    )
    return Response(status_code=204)


@router.patch("/works/{id}/chat/settings")
async def chat_settings(id: str, body: ChatSettings, service=Depends(work_service)):
    """为作品交流区设置可独立编辑的显示标题。"""
    return await service.update_chat(id, body)


@router.get("/search")
async def search(request: Request, service=Depends(work_service)):
    """搜索当前模式、屏蔽规则和实时投影共同允许的作品。"""
    return await service.list(request.query_params, search=True)


@router.get("/search/suggest")
async def suggest(q: str, service=Depends(work_service)):
    """为当前查看者查询搜索建议。"""
    return await search_suggestions(service, q)


@router.get("/collections")
async def list_collections(request: Request, service=Depends(collection_service)):
    """列出当前可见的公开书单。"""
    return await service.list(request.query_params)


@router.get("/me/collections")
async def my_collections(request: Request, service=Depends(collection_service)):
    """列出本人书单，书单条目仍按当前内容模式过滤。"""
    return await service.list(request.query_params, own=True)


@router.post("/collections", status_code=201)
async def create_collection(body: CollectionInput, service=Depends(collection_service)):
    """为已登录主体创建书单。"""
    return await service.save(body)


@router.get("/collections/{id}")
async def get_collection(id: str, service=Depends(collection_service)):
    """读取当前可见书单的资料和条目数量。"""
    return await service.detail(id)


@router.patch("/collections/{id}")
async def patch_collection(
    id: str, body: CollectionInput, service=Depends(collection_service)
):
    """修改书单所有者允许编辑的属性。"""
    return await service.save(body, id)


@router.delete("/collections/{id}", status_code=204)
async def delete_collection(
    id: str, body: DeleteInput = DeleteInput(), service=Depends(collection_service)
):
    """软删除所有者的普通书单。"""
    await service.remove(id, body)
    return Response(status_code=204)


@router.get("/collections/{id}/items")
async def list_collection_items(
    id: str, request: Request, service=Depends(collection_service)
):
    """查看书单中当前可访问的作品。"""
    return await service.items(id, request.query_params)


@router.post("/collections/{id}/items", status_code=201)
async def add_collection_item(
    id: str, body: CollectionItemInput, service=Depends(collection_service)
):
    """将当前可见作品加入所有者自己的书单。"""
    return await service.change_item(id, body.workId, "add", body)


@router.patch("/collections/{id}/items/{workId}")
async def patch_collection_item(
    id: str, workId: str, body: CollectionItemPatch, service=Depends(collection_service)
):
    """修改书单中的条目备注或顺序。"""
    return await service.change_item(id, workId, "edit", body)


@router.delete("/collections/{id}/items/{workId}", status_code=204)
async def delete_collection_item(
    id: str, workId: str, service=Depends(collection_service)
):
    """将作品从所有者自己的书单中移出。"""
    await service.change_item(id, workId, "remove")
    return Response(status_code=204)


@internal.get(
    "/internal/works/{id}/policy", dependencies=[Depends(verify_internal_token)]
)
async def get_policy(id: str, includeDeleted: str | None = None):
    """供平台聊天回查权威策略，草稿始终不会启用交流区。"""
    if not valid_bigint(id):
        raise HTTPException(404, "资源不存在")
    async with get_session_factory()() as session:
        work = await session.get(Work, int(id))
        if (
            work is None
            or work.source_platform != "platform"
            or work.published_at is None
            or (work.deleted_at and includeDeleted != "1")
        ):
            raise HTTPException(404, "资源不存在")
        return await chat_policy(session, work)
