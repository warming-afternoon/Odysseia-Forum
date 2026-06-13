"""Banner申请API路由"""

import json
import logging
import re
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
from banner.channel_sync import ChannelSyncService
from models.thread import Thread
from models.channel import Channel
from shared.enum import TargetType
from shared.redis_client import RedisManager

logger = logging.getLogger(__name__)

DISCORD_LINK_RE = re.compile(
    r"^https?://(?:.*\.)?discord\.com/channels/(\d{17,20})/(\d{17,20})/?$"
)

# 全局变量，将在应用启动时注入
async_session_factory: async_sessionmaker | None = None
banner_config: dict | None = None
main_guild_id: int = 0
bot_token: str = ""


def parse_thread_link(link: str) -> tuple[int, int] | None:
    """解析 thread_link，返回 (guild_id, target_id) 或 None。

    支持 Discord 链接和纯数字 ID。
    纯数字 ID 时使用 main_guild_id 拼接。
    """
    link = link.strip()

    # Discord URL 解析
    m = DISCORD_LINK_RE.match(link)
    if m:
        return int(m.group(1)), int(m.group(2))

    # 纯数字 ID
    if link.isdigit():
        if main_guild_id:
            return main_guild_id, int(link)
        return None

    return None


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
    """提交Banner展示位申请。

    支持 Discord 跳转链接或纯数字 ID。
    传入纯数字 ID 时自动使用主服务器 ID 拼接。
    """
    if not async_session_factory:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务尚未初始化",
        )

    # 解析链接
    parsed = parse_thread_link(request.thread_link)
    if parsed is None:
        return BannerApplicationResponse(
            success=False,
            message="thread_link 格式无效，请提供 Discord 跳转链接或纯数字ID",
        )
    guild_id, target_id = parsed

    user_id = int(current_user.get("id", 0))

    # 创建 ChannelSyncService（如果有 bot_token）
    channel_sync = None
    if bot_token:
        channel_sync = ChannelSyncService(bot_token)

    try:
        async with async_session_factory() as session:
            service = BannerService(session, channel_sync=channel_sync)
            result = await service.validate_and_create_application(
                target_id=target_id,
                guild_id=guild_id,
                applicant_id=user_id,
                cover_image_url=request.cover_image_url,
                target_scope=request.target_scope,
            )

            if not result.success:
                return BannerApplicationResponse(
                    success=False, message=result.message
                )

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
    channel_id: Optional[int] = Query(
        default=None, description="频道ID，不传则获取全频道Banner"
    ),
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """获取当前活跃的Banner轮播列表。"""
    if not async_session_factory:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="服务尚未初始化",
        )

    try:
        async with async_session_factory() as session:
            service = BannerService(session)
            banners = await service.get_active_banners(channel_id=channel_id)

            # 按 target_type 分别查询 guild_id
            thread_tids = [
                b.thread_id
                for b in banners
                if b.target_type == TargetType.THREAD.value
            ]
            channel_ids = [
                b.thread_id
                for b in banners
                if b.target_type == TargetType.CHANNEL.value
            ]

            guild_map: dict[int, int] = {}

            if thread_tids:
                guild_rows = await session.execute(
                    select(Thread.thread_id, Thread.guild_id).where(  # type: ignore[arg-type]
                        Thread.thread_id.in_(thread_tids)  # type: ignore[arg-type]
                    )
                )
                guild_map.update({tid: gid for tid, gid in guild_rows.all()})

            if channel_ids:
                channel_rows = await session.execute(
                    select(Channel.channel_id, Channel.guild_id).where(  # type: ignore[arg-type]
                        Channel.channel_id.in_(channel_ids)  # type: ignore[arg-type]
                    )
                )
                guild_map.update({cid: gid for cid, gid in channel_rows.all()})

            return [
                BannerItem(
                    thread_id=banner.thread_id,
                    title=banner.title,
                    cover_image_url=banner.cover_image_url,
                    channel_id=banner.channel_id if banner.channel_id else 0,
                    guild_id=guild_map.get(banner.thread_id, 0),
                    target_type=banner.target_type,
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
