from __future__ import annotations

import hmac
import logging
import math
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from dto.open_graph import BooklistShareMetadataDTO
from open_graph.booklist_open_graph_service import BooklistOpenGraphService
from open_graph.reindex_queue import OpenGraphReindexQueue


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/share-metadata", tags=["OG 用接口"])

_service_token: str | None = None
_open_graph_service: BooklistOpenGraphService | None = None


def configure_open_graph_router(
    *,
    session_factory: async_sessionmaker,
    redis: Redis,
    config: dict,
) -> None:
    """注入 OG 服务配置与运行期依赖。"""
    global _service_token, _open_graph_service
    raw_token = config.get("service_token")
    _service_token = (
        raw_token.strip()
        if isinstance(raw_token, str) and raw_token.strip()
        else None
    )

    raw_refresh_hours = config.get("refresh_before_expiry_hours", 1)
    try:
        refresh_before_expiry_hours = float(raw_refresh_hours)
        if (
            not math.isfinite(refresh_before_expiry_hours)
            or refresh_before_expiry_hours < 0
        ):
            refresh_before_expiry_hours = 1
    except (TypeError, ValueError):
        refresh_before_expiry_hours = 1

    raw_wait_timeout = config.get("sync_wait_timeout_seconds", 2)
    try:
        sync_wait_timeout_seconds = float(raw_wait_timeout)
        if (
            not math.isfinite(sync_wait_timeout_seconds)
            or sync_wait_timeout_seconds < 0
        ):
            sync_wait_timeout_seconds = 2
    except (TypeError, ValueError):
        sync_wait_timeout_seconds = 2

    _open_graph_service = BooklistOpenGraphService(
        session_factory=session_factory,
        reindex_queue=OpenGraphReindexQueue(redis),
        refresh_before_expiry_hours=refresh_before_expiry_hours,
        sync_wait_timeout_seconds=sync_wait_timeout_seconds,
    )


async def require_open_graph_service_token(
    authorization: Optional[str] = Header(default=None, alias="Authorization"),
) -> None:
    """校验 OG 接口的 Bearer 服务令牌。"""
    if _service_token is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Open Graph 服务未配置",
        )

    scheme, separator, supplied_token = (authorization or "").partition(" ")
    valid = (
        bool(separator)
        and scheme.lower() == "bearer"
        and hmac.compare_digest(supplied_token, _service_token)
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的服务令牌",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.get(
    "/booklists/{booklist_id}",
    response_model=BooklistShareMetadataDTO,
    dependencies=[Depends(require_open_graph_service_token)],
    summary="获取书单分享元数据",
)
async def get_booklist_share_metadata(
    booklist_id: int,
) -> BooklistShareMetadataDTO:
    """返回公开书单的动态分享元数据。"""
    if _open_graph_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Open Graph 服务尚未初始化",
        )

    metadata = await _open_graph_service.get_share_metadata(booklist_id)
    if metadata is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="书单不存在",
        )
    if metadata.image_url is None:
        logger.info(
            "OG 返回空图片，调用方应使用默认图",
            extra={"booklist_id": booklist_id},
        )
    return metadata
