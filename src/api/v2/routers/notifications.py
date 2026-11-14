"""动态通知 HTTP 协议适配。"""

from dto.work.work_actor_dto import WorkActorDTO
from dto.work.work_command_qo import WorkCommandQo
from fastapi import APIRouter, Depends, Query, Request, Response, HTTPException
from api.v2.dependencies import current_member
from work.work_notification_service import WorkNotificationService
from api.v2.dependencies import get_session_factory

router = APIRouter(prefix="/v2", tags=["作品 V2"])


@router.get("/me/notifications")
async def notifications(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
    author_id: int | None = Query(None, ge=1, le=9223372036854775807),
    cursor: str | None = Query(None, max_length=256),
    principal=Depends(current_member),
):
    """撤回公告在原通知显示撤回状态，隐藏作品不出现在通知总数中。"""
    return await WorkNotificationService(get_session_factory()).notifications(
        offset=offset,
        limit=limit,
        unread_only=unread_only,
        author_id=author_id,
        cursor=cursor,
        command=WorkCommandQo(
            actor=WorkActorDTO.from_principal(principal), values={}, operation=None
        ),
    )


async def _ticket_admission():
    """在路由强制权威认证前限制跨进程票据签发速率。"""
    import os
    from shared.redis_client import RedisManager

    if os.environ.get("FORUM_NOTIFICATION_REALTIME_ENABLED", "").lower() != "true":
        raise HTTPException(503, "实时通知尚未启用")
    try:
        limit = int(os.environ.get("FORUM_NOTIFICATION_TICKETS_PER_SECOND", "256"))
        if not 10 <= limit <= 5000:
            raise ValueError("invalid ticket rate")
        redis = RedisManager.get_client()
        count = await redis.eval(
            """local count=redis.call('INCR',KEYS[1]);
            if count==1 then redis.call('EXPIRE',KEYS[1],1) end; return count""",
            1, "forum:notification:ticket:v1:global-rate",
        )
    except Exception as exc:
        raise HTTPException(503, "实时通知暂不可用") from exc
    if int(count) > limit:
        raise HTTPException(429, "实时连接繁忙，请稍后重试", headers={"Retry-After": "1"})


@router.post("/me/notifications/socket-ticket", dependencies=[Depends(_ticket_admission)])
async def socket_ticket(request: Request, response: Response, principal=Depends(current_member)):
    """签发一次性短期网关票据；登录凭据不出现在 WS URL。"""
    from identity.auth_runtime import get_auth_service
    from work.notification_ticket import NotificationTicketService

    token = request.headers.get("Authorization", "")[7:].strip()
    ticket = await NotificationTicketService(get_auth_service()).issue(
        token, str(principal["id"])
    )
    response.headers["Cache-Control"] = "no-store"
    return {"ticket": ticket, "expires_in": NotificationTicketService.TTL_SECONDS}


@router.get("/me/notifications/unread-count")
async def unread_count(principal=Depends(current_member)):
    """统计当前可见的未读通知。"""
    return await WorkNotificationService(get_session_factory()).unread_count(
        WorkCommandQo(
            actor=WorkActorDTO.from_principal(principal), values={}, operation=None
        )
    )


@router.post("/me/notifications/read-all")
async def read_all(principal=Depends(current_member)):
    """标记操作时快照中可见的所有通知。"""
    return await WorkNotificationService(get_session_factory()).read_all(
        WorkCommandQo(
            actor=WorkActorDTO.from_principal(principal), values={}, operation=None
        )
    )


@router.post("/me/notifications/{notification_id}/read")
async def read_notification(notification_id: int, principal=Depends(current_member)):
    """只允许当前用户标记自己的通知。"""
    return await WorkNotificationService(get_session_factory()).read_notification(
        notification_id=notification_id,
        command=WorkCommandQo(
            actor=WorkActorDTO.from_principal(principal), values={}, operation=None
        ),
    )
