import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from api.v1.dependencies.security import get_current_user, require_auth
from api.v1.schemas.notification import (
    MarkReadResponse,
    NotificationItem,
    NotificationList,
    UnreadCountResponse,
)
from api.v1.schemas.search import LatestUpdate
from api.v1.utils import ThreadDetailBuilder
from core.notification_repository import NotificationRepository
from core.thread_presentation_service import ThreadPresentationService
from core.thread_repository import ThreadRepository
from models import ThreadUpdate
from shared.database import AsyncSessionFactory

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/notifications",
    tags=["动态通知"],
    dependencies=[Depends(require_auth)],
)


@router.get("", response_model=NotificationList, summary="获取动态通知列表")
async def list_notifications(
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    unread_only: bool = Query(default=False),
    current_user: dict[str, Any] = Depends(get_current_user),
) -> NotificationList:
    """分页返回作品更新与作者新作通知。"""
    user_id = int(current_user["id"])
    async with AsyncSessionFactory() as session:
        notification_repository = NotificationRepository(session)
        notifications, total, unread_count = (
            await notification_repository.list_for_user(
                user_id, unread_only, limit, offset
            )
        )
        thread_ids = list({item.thread_id for item in notifications})
        threads = await ThreadRepository(session).get_threads_by_ids_with_tags(
            thread_ids
        )
        thread_map = {thread.thread_id: thread for thread in threads}
        context = await ThreadPresentationService(session).load(user_id, threads)
        thread_builder = ThreadDetailBuilder()

        update_ids = {
            item.event_source_id
            for item in notifications
            if item.event_type == "thread_update"
        }
        update_map: dict[int, ThreadUpdate] = {}
        if update_ids:
            statement = select(ThreadUpdate).where(ThreadUpdate.id.in_(update_ids))
            update_map = {
                update.id: update
                for update in (
                    await session.execute(statement)
                ).scalars().all()
                if update.id is not None
            }

        results: list[NotificationItem] = []
        for item in notifications:
            if item.id is None or (thread := thread_map.get(item.thread_id)) is None:
                continue
            update_response = None
            update_record = update_map.get(item.event_source_id)
            if update_record is not None:
                message_link = None
                if update_record.message_id is not None:
                    message_link = (
                        f"https://discord.com/channels/{thread.guild_id}/"
                        f"{thread.thread_id}/{update_record.message_id}"
                    )
                update_response = LatestUpdate(
                    id=update_record.id,
                    description=update_record.description,
                    version=update_record.version,
                    message_link=message_link,
                    source_message_at=update_record.source_message_at,
                    published_at=update_record.published_at,
                )
            results.append(
                NotificationItem(
                    id=item.id,
                    type=item.event_type,
                    thread=thread_builder.build(
                        thread,
                        set(),
                        presentation_context=context,
                    ),
                    update=update_response,
                    created_at=item.created_at,
                    read_at=item.read_at,
                )
            )
    return NotificationList(
        results=results,
        total=total,
        unread_count=unread_count,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="获取动态通知未读数",
)
async def get_unread_count(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> UnreadCountResponse:
    """返回当前用户全部动态通知的未读数量。"""
    async with AsyncSessionFactory() as session:
        count = await NotificationRepository(session).unread_count(
            int(current_user["id"])
        )
    return UnreadCountResponse(unread_count=count)


@router.post(
    "/threads/{thread_id}/read",
    response_model=MarkReadResponse,
    summary="按作品标记动态通知已读",
)
async def mark_thread_read(
    thread_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
) -> MarkReadResponse:
    """幂等标记用户在指定作品下的全部动态通知。"""
    async with AsyncSessionFactory() as session:
        count = await NotificationRepository(session).mark_thread_read(
            int(current_user["id"]), thread_id
        )
        await session.commit()
    return MarkReadResponse(thread_id=thread_id, marked_read=count)


@router.post(
    "/read-all",
    response_model=MarkReadResponse,
    summary="将全部动态通知标为已读",
)
async def mark_all_read(
    current_user: dict[str, Any] = Depends(get_current_user),
) -> MarkReadResponse:
    """幂等标记当前用户的全部动态通知。"""
    async with AsyncSessionFactory() as session:
        count = await NotificationRepository(session).mark_all_read(
            int(current_user["id"])
        )
        await session.commit()
    return MarkReadResponse(marked_read=count)

