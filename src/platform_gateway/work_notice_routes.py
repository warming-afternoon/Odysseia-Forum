"""平台作品公告、规则和确认的 HTTP 路由。"""

import re
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from api.v2.dependencies import get_session_factory
from platform_gateway.http import PlatformRoute, invalid
from platform_gateway.principal import gateway_principal
from platform_gateway.work_notice_service import WorkNoticeService


router = APIRouter(route_class=PlatformRoute)


class RulesInput(BaseModel):
    """当前作品规则正文。"""

    model_config = ConfigDict(extra="forbid")
    body: str = Field(max_length=5000)


class AckInput(BaseModel):
    """读者所看到的公告或规则版本。"""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["announcement", "rules"]
    version: Annotated[StrictInt, Field(gt=0, lt=2**63)]

    @field_validator("version", mode="before")
    @classmethod
    def parse_version(cls, value):
        """兼容客户端以数字字符串提交的版本。"""
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
            return int(value)
        return value


async def notice_service(principal=Depends(gateway_principal)):
    """在一个请求会话内完成作品读取、业务写入和本地通知。"""
    async with get_session_factory()() as session:
        yield WorkNoticeService(session, principal)


@router.get("/works/{id}/announcements")
async def announcements(
    id: str,
    cursor: str | None = Query(None, max_length=128),
    limit: int = Query(20, ge=1, le=100),
    service=Depends(notice_service),
):
    """获取当前模式可见作品的公告历史。"""
    return await service.list_announcements(id, cursor, limit)


@router.post("/works/{id}/announcements", status_code=201)
async def publish_announcement(
    id: str, request: Request, body: dict = Body(...), service=Depends(notice_service)
):
    """发布新公告并支持使用相同请求重试。"""
    key = request.headers.get("Idempotency-Key")
    if key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key):
        raise invalid("Idempotency-Key", "幂等键无效")
    return await service.publish_announcement(id, body, key)


@router.patch("/works/{id}/announcements/{announcementId}")
@router.delete("/works/{id}/announcements/{announcementId}", status_code=204)
async def change_announcement(
    id: str,
    announcementId: str,
    request: Request,
    body: dict = Body(default={}),
    service=Depends(notice_service),
):
    """编辑或撤回已发布公告，可指定修改前的 revision。"""
    revision = request.headers.get("If-Match")
    if revision is not None:
        revision = revision.strip()
        if revision.startswith('"') and revision.endswith('"'):
            revision = revision[1:-1]
        if (
            not re.fullmatch(r"[0-9]{1,10}", revision)
            or not 1 <= int(revision) <= 2**31 - 1
        ):
            raise invalid("If-Match", "公告修订版本无效")
        revision = int(revision)
    withdraw = request.method == "DELETE"
    result = await service.change_announcement(
        id, announcementId, body, revision, withdraw=withdraw
    )
    return Response(status_code=204) if withdraw else result


@router.get("/works/{id}/rules")
async def rules(id: str, service=Depends(notice_service)):
    """返回当前作品规则及版本。"""
    return await service.rules(id)


@router.put("/works/{id}/rules")
async def save_rules(id: str, body: RulesInput, service=Depends(notice_service)):
    """保存当前规则并通知有效关注者。"""
    return await service.save_rules(id, body.body)


@router.post("/works/{id}/ack", status_code=204)
async def acknowledge(id: str, body: AckInput, service=Depends(notice_service)):
    """确认用户实际看到的公告或规则。"""
    await service.acknowledge(id, body.kind, body.version)
    return Response(status_code=204)
