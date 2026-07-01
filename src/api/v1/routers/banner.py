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
from api.v1.utils.preferences_utils import get_user_preferences_cached
from banner.banner_service import BannerService
from banner.channel_sync import ChannelSyncService
from core.thread_repository import ThreadRepository
from dto.preferences import UserSearchPreferencesDTO
from models import BannerCarousel
from models.thread import Thread
from models.channel import Channel
from shared.enum import TargetType
from shared.redis_client import RedisManager
from shared.thread_link_parser import ThreadLinkParser

logger = logging.getLogger(__name__)

# 全局变量，将在应用启动时注入
async_session_factory: async_sessionmaker | None = None
banner_config: dict | None = None
main_guild_id: int = 0
bot_token: str = ""


_EXCLUDE_KEYWORD_SPLIT_RE = re.compile(r"[,，/\\\s]+")


def _filter_thread_banners_by_prefs(
    thread_banners: List[BannerCarousel],
    thread_map: dict[int, Thread],
    prefs: UserSearchPreferencesDTO,
) -> List[BannerCarousel]:
    """根据用户搜索偏好筛选 thread 类型 banner。

    对应 search 接口的 exclude_authors / exclude_tags / exclude_keywords 过滤。
    仅排除明确命中的 banner，prefs 为空时不做过滤。
    """
    # 准备排除数据
    exclude_authors: set[int] = set(prefs.exclude_authors or [])
    exclude_tags: set[str] = (
        {t.lower() for t in prefs.exclude_tags} if prefs.exclude_tags else set()
    )
    exclude_keywords_raw: str = (prefs.exclude_keywords or "").strip()

    exclude_keywords: list[str] = []
    if exclude_keywords_raw:
        exclude_keywords = [
            kw.strip().lower()
            for kw in _EXCLUDE_KEYWORD_SPLIT_RE.split(exclude_keywords_raw)
            if kw.strip()
        ]

    # 无任何排除条件，直接返回
    if not exclude_authors and not exclude_tags and not exclude_keywords:
        return thread_banners

    filtered: List[BannerCarousel] = []
    for banner in thread_banners:
        thread = thread_map.get(banner.thread_id)
        if thread is None:
            # 线程不存在（可能已被删除），仍然保留 banner
            filtered.append(banner)
            continue

        # 检查 exclude_authors
        if exclude_authors and thread.author_id in exclude_authors:
            continue

        # 检查 exclude_tags
        if exclude_tags:
            thread_tag_names = {t.name.lower() for t in (thread.tags or [])}
            if thread_tag_names & exclude_tags:
                continue

        # 检查 exclude_keywords（匹配 title + first_message_excerpt）
        if exclude_keywords:
            search_text = thread.title or ""
            if thread.first_message_excerpt:
                search_text += " " + thread.first_message_excerpt
            search_text_lower = search_text.lower()
            if any(kw in search_text_lower for kw in exclude_keywords):
                continue

        filtered.append(banner)

    return filtered


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
    parsed = ThreadLinkParser.parse_thread_link(request.thread_link, main_guild_id)
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

            # ── 加载用户搜索偏好 ──
            user_id = int(current_user.get("id", 0)) if current_user else 0
            prefs = None
            if user_id:
                try:
                    redis_client = RedisManager.get_client()
                    prefs = await get_user_preferences_cached(
                        redis_client, async_session_factory, user_id, main_guild_id
                    )
                except Exception:
                    logger.warning("读取用户偏好失败，跳过偏好筛选", exc_info=True)

            # 按 target_type 分离
            thread_banners = [
                b for b in banners if b.target_type == TargetType.THREAD.value
            ]
            channel_banners = [
                b for b in banners if b.target_type == TargetType.CHANNEL.value
            ]

            thread_tids = [b.thread_id for b in thread_banners]
            channel_cids = [b.thread_id for b in channel_banners]

            guild_map: dict[int, int] = {}
            thread_map: dict[int, Thread] = {}

            # 查询 thread 类型对应的 Thread 对象（含 tags 和 guild_id）
            if thread_tids:
                thread_repo = ThreadRepository(session)
                threads = await thread_repo.get_threads_by_ids_with_tags(thread_tids)
                for t in threads:
                    guild_map[t.thread_id] = t.guild_id
                    thread_map[t.thread_id] = t

            # 查询 channel 类型对应的 guild_id
            if channel_cids:
                channel_rows = await session.execute(
                    select(Channel.channel_id, Channel.guild_id).where(  # type: ignore[arg-type]
                        Channel.channel_id.in_(channel_cids)  # type: ignore[arg-type]
                    )
                )
                guild_map.update({cid: gid for cid, gid in channel_rows.all()})

            # ── 应用用户偏好筛选（仅 thread 类型） ──
            if prefs and thread_banners:
                thread_banners = _filter_thread_banners_by_prefs(
                    thread_banners, thread_map, prefs
                )

            # 合并筛选后的 banners（channel 类型始终保留）
            filtered_banners = thread_banners + channel_banners

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
                for banner in filtered_banners
            ]
    except Exception as e:
        logger.error(f"获取Banner列表时出错: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="获取Banner列表时出错",
        )
