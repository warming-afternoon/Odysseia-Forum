"""作品公告 HTTP 协议适配。"""

import re

from dto.work.work_actor_dto import WorkActorDTO
from dto.work.work_command_qo import WorkCommandQo
from fastapi import Request, APIRouter, Depends, Body, Query, HTTPException
from api.v2.dependencies import current_member
from work.work_announcement_service import WorkAnnouncementService
from api.v2.dependencies import get_session_factory

router = APIRouter(prefix="/v2", tags=["作品 V2"])


@router.get("/works/{work_id}/announcements")
async def announcements(
    work_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    principal=Depends(current_member),
):
    """历史公告可读，不为新关注者补发通知。"""
    return await WorkAnnouncementService(get_session_factory()).announcements(
        work_id=work_id,
        offset=offset,
        limit=limit,
        command=WorkCommandQo(
            actor=WorkActorDTO.from_principal(principal), values={}, operation=None
        ),
    )


@router.post("/works/{work_id}/announcements")
async def publish_announcement(
    work_id: int, request: Request, body: dict = Body(...), principal=Depends(current_member)
):
    """可传 Idempotency-Key 防止重试重复发布。"""
    key = request.headers.get("Idempotency-Key")
    if key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key):
        raise HTTPException(400, "Idempotency-Key 无效")
    return await WorkAnnouncementService(get_session_factory()).publish_announcement(
        work_id=work_id,
        idempotency_key=key,
        command=WorkCommandQo(
            actor=WorkActorDTO.from_principal(principal), values=body, operation=None
        ),
    )


@router.patch("/works/{work_id}/announcements/{announcement_id}")
@router.delete("/works/{work_id}/announcements/{announcement_id}")
async def edit_announcement(
    work_id: int,
    announcement_id: int,
    request: Request,
    body: dict = Body(default={}),
    principal=Depends(current_member),
):
    """可用 If-Match 指定版本；未传时保留现有客户端兼容行为。"""
    match = request.headers.get("If-Match")
    expected_revision = None
    if match is not None:
        value = match.strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        if not value.isascii() or not value.isdecimal() or not 1 <= int(value) <= 2147483647:
            raise HTTPException(400, "If-Match 版本无效")
        expected_revision = int(value)
    return await WorkAnnouncementService(get_session_factory()).edit_announcement(
        work_id=work_id,
        announcement_id=announcement_id,
        expected_revision=expected_revision,
        command=WorkCommandQo(
            actor=WorkActorDTO.from_principal(principal),
            values=body,
            operation="withdraw" if request.method == "DELETE" else "edit",
        ),
    )
