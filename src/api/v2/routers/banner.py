"""首页推荐 HTTP 协议适配。"""

from typing import Annotated
from pydantic import Field
from api.v2.schemas.work_banner_application_request import WorkBannerApplicationRequest
from api.v2.schemas.discord_banner_application_request import DiscordBannerApplicationRequest
from dto.work.work_actor_dto import WorkActorDTO
from dto.work.work_command_qo import WorkCommandQo
from fastapi import APIRouter, Depends, Body, Query, Path
from api.v2.dependencies import current_member
from work.work_banner_service import WorkBannerService
from api.v2.dependencies import get_session_factory

router = APIRouter(prefix="/v2", tags=["作品 V2"])


@router.get("/banner")
async def banner(
    channel_ids: list[Annotated[int, Field(gt=0, lt=2**63)]] | None = Query(default=None, max_length=100),
    principal=Depends(current_member),
):
    """作品 Banner 受内容偏好约束，频道赛事 Banner 保持现有展示权限。"""
    return await WorkBannerService(get_session_factory()).banner(
        command=WorkCommandQo(
            actor=WorkActorDTO.from_principal(principal), values={"channel_ids": channel_ids}, operation=None
        )
    )


@router.post("/works/{work_id}/banner-applications", status_code=201)
async def apply_banner(
    work_id: Annotated[int, Path(gt=0, lt=2**63)], body: WorkBannerApplicationRequest, principal=Depends(current_member)
):
    """作者提交统一作品 Banner 申请。"""
    return await WorkBannerService(get_session_factory()).apply(
        work_id,
        WorkCommandQo(actor=WorkActorDTO.from_principal(principal), values=body.model_dump()),
    )


@router.get("/me/banner-applications")
async def my_banner_applications(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    principal=Depends(current_member),
):
    """查询本人真实 Banner 申请及当前排队状态。"""
    return await WorkBannerService(get_session_factory()).applications(
        WorkCommandQo(actor=WorkActorDTO.from_principal(principal)), offset, limit
    )


@router.get("/admin/banner-applications")
async def admin_banner_applications(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    principal=Depends(current_member),
):
    """管理查询既有申请审核队列。"""
    return await WorkBannerService(get_session_factory()).applications(
        WorkCommandQo(actor=WorkActorDTO.from_principal(principal)),
        offset,
        limit,
        admin=True,
    )


@router.patch("/admin/banner-applications/{application_id}")
async def review_banner(
    application_id: int, body: dict = Body(...), principal=Depends(current_member)
):
    """管理审批后直接进入真实轮播或候补。"""
    return await WorkBannerService(get_session_factory()).review(
        application_id,
        WorkCommandQo(actor=WorkActorDTO.from_principal(principal), values=body),
    )


@router.get("/works/{work_id}/banner-scopes")
async def banner_scopes(work_id: int, principal=Depends(current_member)):
    """提供当前作者允许选择的展示范围。"""
    return await WorkBannerService(get_session_factory()).scopes(
        work_id, WorkCommandQo(actor=WorkActorDTO.from_principal(principal))
    )


@router.get("/banner/scopes")
async def available_banner_scopes(principal=Depends(current_member)):
    """申请表只读取当前成员可选的展示范围。"""
    return {"items": WorkBannerService(get_session_factory()).available_scopes(
        WorkActorDTO.from_principal(principal)
    )}


@router.post("/banner/discord-applications", status_code=201)
async def apply_discord_banner(body: DiscordBannerApplicationRequest, principal=Depends(current_member)):
    """保留 Discord 作品链接及频道赛事申请入口。"""
    return await WorkBannerService(get_session_factory()).apply_discord(
        WorkCommandQo(actor=WorkActorDTO.from_principal(principal), values=body.model_dump())
    )
