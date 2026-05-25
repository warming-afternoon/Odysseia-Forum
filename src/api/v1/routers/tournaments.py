"""赛事路由（BOT 机机通信）"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.v1.dependencies.security import require_api_key
from api.v1.schemas.base import PaginatedResponse
from api.v1.schemas.booklist.booklist_items_delete_request import (
    BooklistItemsDeleteRequest,
)
from api.v1.schemas.tournament import (
    TournamentCreateRequest,
    TournamentCreateResponse,
    TournamentItemAddData,
    TournamentItemsAddRequest,
    TournamentItemUpdateRequest,
    TournamentUpdateRequest,
)
from shared.database import AsyncSessionFactory
from tournament.tournament_service import TournamentService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tournament", tags=["赛事"])


@router.post("/create", summary="创建赛事书单", response_model=TournamentCreateResponse)
async def create_tournament(
    request: TournamentCreateRequest,
    api_key: bool = Depends(require_api_key),
):
    """
    创建赛事书单。同一 tournament_channel_id 多次调用幂等返回已有书单。

    - **tournament_channel_id**: 赛事关联的 Discord 频道ID（唯一标识）
    - **owner_id**: 赛事举办者 Discord 用户ID（即书单 owner）
    - **title**: 赛事标题
    """
    try:
        async with AsyncSessionFactory() as session:
            service = TournamentService(session)
            booklist, created = await service.create_or_get_tournament(request)

        return TournamentCreateResponse(
            message="赛事书单创建成功" if created else "赛事书单已存在",
            booklist_id=booklist.id,  # type: ignore[arg-type]
            title=booklist.title,
            tournament_channel_id=request.tournament_channel_id,
            created=created,
        )
    except Exception as e:
        logger.error(f"创建赛事书单失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="创建赛事书单失败",
        )


@router.post(
    "/{tournament_channel_id}/items/add",
    summary="向赛事添加参赛帖子",
)
async def add_tournament_items(
    tournament_channel_id: int,
    request: TournamentItemsAddRequest,
    api_key: bool = Depends(require_api_key),
):
    """
    向赛事书单批量添加参赛帖子。

    - **tournament_channel_id**: 赛事频道ID
    - **items**: 帖子列表，每项包含 thread_id（必填）、comment（可选）、display_order（可选）、tournament_participated_at（可选）
    """
    try:
        async with AsyncSessionFactory() as session:
            service = TournamentService(session)
            added = await service.add_items(tournament_channel_id, request.items)

        return {
            "message": f"成功添加 {len(added)} 个参赛帖子",
            "added_count": len(added),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"添加赛事帖子失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="添加赛事帖子失败",
        )


@router.delete(
    "/{tournament_channel_id}/items/delete",
    summary="从赛事移除参赛帖子",
)
async def remove_tournament_items(
    tournament_channel_id: int,
    request: BooklistItemsDeleteRequest,
    api_key: bool = Depends(require_api_key),
):
    """
    从赛事书单批量移除参赛帖子。

    - **tournament_channel_id**: 赛事频道ID
    - **thread_ids**: 要移除的帖子ID列表
    """
    try:
        thread_ids = [int(tid) for tid in request.thread_ids]
        async with AsyncSessionFactory() as session:
            service = TournamentService(session)
            deleted_count = await service.remove_items(tournament_channel_id, thread_ids)

        return {
            "message": f"成功从赛事移除 {deleted_count} 个帖子",
            "deleted_count": deleted_count,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"移除赛事帖子失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="移除赛事帖子失败",
        )


@router.delete("/{tournament_channel_id}", summary="删除赛事书单")
async def delete_tournament(
    tournament_channel_id: int,
    api_key: bool = Depends(require_api_key),
):
    """
    删除整个赛事书单及其所有关联帖子。

    - **tournament_channel_id**: 赛事频道ID
    """
    try:
        async with AsyncSessionFactory() as session:
            service = TournamentService(session)
            success = await service.delete_tournament(tournament_channel_id)

        if not success:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="赛事书单不存在",
            )

        return {"message": "赛事书单删除成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除赛事书单失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="删除赛事书单失败",
        )


@router.patch("/{tournament_channel_id}", summary="更新赛事书单详情")
async def update_tournament(
    tournament_channel_id: int,
    request: TournamentUpdateRequest,
    api_key: bool = Depends(require_api_key),
):
    """
    更新赛事书单的标题、描述、封面图、可见性等元信息。

    - **tournament_channel_id**: 赛事频道ID
    - 请求体中的字段均为可选，仅更新传入的字段
    """
    try:
        async with AsyncSessionFactory() as session:
            service = TournamentService(session)
            booklist = await service.update_tournament(tournament_channel_id, request)

        return {
            "message": "赛事书单已更新",
            "booklist_id": booklist.id,  # type: ignore[arg-type]
            "title": booklist.title,
            "description": booklist.description,
            "cover_image_url": booklist.cover_image_url,
            "is_public": booklist.is_public,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新赛事书单失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新赛事书单失败",
        )


@router.patch(
    "/{tournament_channel_id}/items/{thread_id}",
    summary="更新参赛帖子信息",
)
async def update_tournament_item(
    tournament_channel_id: int,
    thread_id: int,
    update_data: TournamentItemUpdateRequest,
    api_key: bool = Depends(require_api_key),
):
    """
    更新赛事书单中某个帖子的信息。

    - **tournament_channel_id**: 赛事频道ID
    - **thread_id**: 帖子ID
    - **update_data**: 要更新的字段（comment, display_order, tournament_participated_at）
    """
    try:
        async with AsyncSessionFactory() as session:
            service = TournamentService(session)
            updated = await service.update_item(
                tournament_channel_id, thread_id, update_data
            )

        if not updated:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="指定的帖子不在该赛事中",
            )

        return {"message": "赛事项更新成功"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"更新赛事项失败: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="更新赛事项失败",
        )
