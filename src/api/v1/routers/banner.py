"""Banner申请API路由"""

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from api.v1.dependencies.security import get_current_user, require_auth
from api.v1.schemas.banner import (
    BannerApplicationRequest,
    BannerApplicationResponse,
    BannerItem,
)
from banner.banner_service import BannerService
from models.thread import Thread
from shared.redis_client import RedisManager

logger = logging.getLogger(__name__)

# 全局变量，将在应用启动时注入
async_session_factory: async_sessionmaker | None = None
banner_config: dict | None = None


router = APIRouter(
    prefix="/banner", tags=["Banner管理"], dependencies=[Depends(require_auth)]
)


@router.post(
    "/apply", response_model=BannerApplicationResponse, summary="提交Banner申请"
)
async def apply_banner(
    request: BannerApplicationRequest,
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    提交Banner展示位申请

    - 只能为自己的帖子申请
    - 帖子必须已被索引
    - 封面图必须是有效的URL
    - 申请成功后会自动发送审核消息到指定子区
    """
    if not async_session_factory:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="服务尚未初始化"
        )

    # 验证帖子ID格式
    thread_id_str = request.thread_id.strip()
    if not thread_id_str.isdigit():
        return BannerApplicationResponse(success=False, message="帖子ID必须是纯数字")

    thread_id = int(thread_id_str)
    user_id = int(current_user.get("id", 0))

    try:
        async with async_session_factory() as session:
            service = BannerService(session)
            result = await service.validate_and_create_application(
                thread_id=thread_id,
                applicant_id=user_id,
                cover_image_url=request.cover_image_url,
                target_scope=request.target_scope,
            )

            if not result.success:
                return BannerApplicationResponse(success=False, message=result.message)

            application = result.application

            # 将审核消息放入 Redis 队列，由 Bot 进程消费发送
            if banner_config and application:
                try:
                    redis = RedisManager.get_client()
                    await redis.lpush(  # type: ignore[return-type]
                        "banner:review:queue",
                        json.dumps({"application_id": application.id}),
                    )
                except Exception:
                    logger.warning(
                        f"审核消息入队失败，但申请已创建。申请ID: {application.id}",
                        exc_info=True,
                    )
            else:
                logger.warning("Banner配置未初始化，跳过审核消息入队")

            return BannerApplicationResponse(
                success=True,
                message=result.message,
                application_id=application.id if application else None,
            )

    except Exception as e:
        logger.error(f"处理Banner申请时出错: {e}", exc_info=True)
        return BannerApplicationResponse(
            success=False, message=f"提交申请时出错: {str(e)}"
        )


@router.get(
    "/active",
    response_model=List[BannerItem],
    summary="获取当前活跃的Banner列表",
)
async def get_active_banners(
    channel_id: Optional[int] = Query(default=None, description="频道ID，不传则获取全频道Banner"),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """
    获取当前活跃的Banner轮播列表

    - channel_id: 可选，指定频道ID获取该频道+全频道的Banner
    - 返回的 guild_id + thread_id 可用于前端构建 Discord 跳转链接
    """
    if not async_session_factory:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="服务尚未初始化"
        )

    try:
        async with async_session_factory() as session:
            service = BannerService(session)
            banners = await service.get_active_banners(channel_id=channel_id)

            # 批量查询 guild_id，用于前端构建 Discord 跳转链接
            guild_by_thread: dict[int, int] = {}
            if banners:
                banner_tids = [b.thread_id for b in banners]
                guild_rows = await session.execute(
                    select(Thread.thread_id, Thread.guild_id).where(  # type: ignore[arg-type]
                        Thread.thread_id.in_(banner_tids)  # type: ignore[arg-type]
                    )
                )
                guild_by_thread = {tid: gid for tid, gid in guild_rows.all()}

            return [
                BannerItem(
                    thread_id=banner.thread_id,
                    title=banner.title,
                    cover_image_url=banner.cover_image_url,
                    channel_id=banner.channel_id if banner.channel_id else 0,
                    guild_id=guild_by_thread.get(banner.thread_id, 0),
                    start_time=banner.start_time,
                    end_time=banner.end_time,
                )
                for banner in banners
            ]
    except Exception as e:
        logger.error(f"获取Banner列表时出错: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取Banner列表时出错",
        )
