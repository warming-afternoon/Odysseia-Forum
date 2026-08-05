from __future__ import annotations

import logging

from sqlalchemy.ext.asyncio import async_sessionmaker

from core.booklist_item_repository import BooklistItemRepository
from core.booklist_repository import BooklistRepository
from dto.open_graph import BooklistCoverCandidateDTO, BooklistShareMetadataDTO
from open_graph.reindex_queue import OpenGraphReindexQueue
from shared.image_url_utils import (
    discord_attachment_identity,
    discord_expiry_remaining_seconds,
    is_http_url,
)


logger = logging.getLogger(__name__)


class BooklistOpenGraphService:
    """生成公开书单的动态 Open Graph 元数据。"""

    def __init__(
        self,
        session_factory: async_sessionmaker,
        reindex_queue: OpenGraphReindexQueue,
        refresh_before_expiry_hours: float = 1,
        sync_wait_timeout_seconds: float = 2,
    ):
        self.session_factory = session_factory
        self.reindex_queue = reindex_queue
        self.refresh_before_expiry_seconds = max(
            0.0, refresh_before_expiry_hours * 3600
        )
        self.sync_wait_timeout_seconds = max(0.0, sync_wait_timeout_seconds)

    async def get_share_metadata(
        self, booklist_id: int
    ) -> BooklistShareMetadataDTO | None:
        """返回公开书单元数据，并按 URL 状态执行混合刷新。"""
        # 先取得脱离数据库会话的书单快照和首个有图候选
        loaded = await self._load_metadata_and_candidate(booklist_id)
        if loaded is None:
            return None
        metadata, candidate, has_custom_cover = loaded

        # 自定义封面无需检查 Discord 签名；无图书单也不应触发重索引
        if has_custom_cover or candidate is None:
            return metadata

        # 非 Discord 附件没有 ex 签名生命周期，验证为图片后即可直接返回
        image_url = candidate.image_url
        if discord_attachment_identity(image_url) is None:
            metadata.image_url = image_url
            return metadata

        # 剩余有效期超过刷新窗口时避免任何 Redis 与 Discord 额外工作
        remaining_seconds = discord_expiry_remaining_seconds(image_url)
        if (
            remaining_seconds is not None
            and remaining_seconds > self.refresh_before_expiry_seconds
        ):
            metadata.image_url = image_url
            return metadata

        # 最后一小时继续返回可用旧图，同时只负责入队而不等待 Bot
        if remaining_seconds is not None and remaining_seconds >= 0:
            try:
                await self.reindex_queue.get_or_enqueue(candidate.thread_id)
            except Exception:
                logger.exception(
                    "OG 临期图片重索引入队失败",
                    extra={"thread_id": candidate.thread_id},
                )
            metadata.image_url = image_url
            return metadata

        # 已过期或无法解析 ex 时进入最多两秒的同步确认分支
        return await self._refresh_expired_candidate(
            booklist_id=booklist_id,
            stale_candidate=candidate,
            metadata=metadata,
        )

    async def _load_metadata_and_candidate(
        self, booklist_id: int
    ) -> tuple[
        BooklistShareMetadataDTO, BooklistCoverCandidateDTO | None, bool
    ] | None:
        """在一次会话中加载书单快照与首个有效图片候选。"""
        async with self.session_factory() as session:
            # 私有书单与不存在书单在服务层统一隐藏，防止接口泄露存在性
            booklist_repository = BooklistRepository(session)
            booklist = await booklist_repository.get_booklist(booklist_id)
            if booklist is None or not booklist.is_public:
                return None

            # DTO 只保留 OG 所需标量，离开会话后不会携带 ORM 状态
            metadata = BooklistShareMetadataDTO(
                title=booklist.title,
                description=booklist.description,
                image_url=None,
                item_count=booklist.item_count,
                updated_at=booklist.updated_at,
            )

            # 合法 HTTP(S) 自定义封面由书单所有者负责，不解析 Discord 签名
            if is_http_url(booklist.cover_image_url):
                metadata.image_url = booklist.cover_image_url
                return metadata, None, True

            # 未设置自定义封面时按书单默认排序扫描可见且有效的帖子
            item_repository = BooklistItemRepository(session)
            candidate = await item_repository.get_open_graph_cover_candidate(
                booklist_id=booklist_id,
                default_sort_method=booklist.default_sort_method,
                default_sort_order=booklist.default_sort_order,
            )
            if candidate is not None:
                metadata.image_url = candidate.image_url
            return metadata, candidate, False

    async def _refresh_expired_candidate(
        self,
        *,
        booklist_id: int,
        stale_candidate: BooklistCoverCandidateDTO,
        metadata: BooklistShareMetadataDTO,
    ) -> BooklistShareMetadataDTO | None:
        """等待过期图片重索引，并仅返回数据库中的新有效 URL。"""
        # 过期旧图绝不能进入响应，任何失败或超时均保持 image_url 为空
        metadata.image_url = None
        try:
            # 并发请求复用同一 pending job_id，避免重复调用 Discord
            job_id = await self.reindex_queue.get_or_enqueue(stale_candidate.thread_id)
            if not job_id:
                return metadata
            # 结果键采用轮询读取，同一个结果可同时被多个 API 请求观察
            result = await self.reindex_queue.wait_for_result(
                job_id, self.sync_wait_timeout_seconds
            )
        except Exception:
            logger.exception(
                "OG 过期图片刷新协调失败",
                extra={"thread_id": stale_candidate.thread_id},
            )
            return metadata

        if not result or result.get("status") != "success":
            return metadata

        # Bot 成功只代表事务已提交；API 必须重查数据库后自行验证新 URL
        reloaded = await self._load_metadata_and_candidate(booklist_id)
        if reloaded is None:
            return None
        fresh_metadata, fresh_candidate, has_custom_cover = reloaded
        if has_custom_cover:
            return fresh_metadata
        if fresh_candidate is None:
            fresh_metadata.image_url = None
            return fresh_metadata
        if fresh_candidate.image_url == stale_candidate.image_url:
            fresh_metadata.image_url = None
            return fresh_metadata

        # Discord 新 URL 必须带有可解析且尚未到期的 ex；非 Discord URL 可直返
        remaining_seconds = discord_expiry_remaining_seconds(
            fresh_candidate.image_url
        )
        if discord_attachment_identity(fresh_candidate.image_url) is not None:
            if remaining_seconds is None or remaining_seconds <= 0:
                fresh_metadata.image_url = None
        return fresh_metadata
