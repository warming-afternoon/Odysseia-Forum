import logging
from collections import defaultdict
from typing import Any, Dict, List, cast

from sqlalchemy import ColumnElement, func
from sqlalchemy.sql import Select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from api.v1.schemas.tags import (
    ChannelTagInfo,
    TagStatItem,
    TagStatsRequest,
    TagStatsResponse,
)
from core.thread_repository import ThreadRepository
from models import Tag, Thread, ThreadTagLink

logger = logging.getLogger(__name__)


class TagService:
    """处理标签聚合与统计等复杂跨表业务逻辑的服务。"""

    def __init__(self, session: AsyncSession, channel_mappings: Dict[int, List[Dict]]):
        """
        初始化标签服务。
        """
        self.session = session
        self.channel_mappings = channel_mappings

    async def aggregate_tag_stats(self, request: TagStatsRequest) -> TagStatsResponse:
        """聚合计算标签统计信息"""
        # 先确定请求中的目标频道范围
        requested_channels = set(request.channel_ids) if request.channel_ids else None
        extended_channel_ids = set(request.channel_ids) if request.channel_ids else set()

        # 若包含虚拟标签，则补充映射源频道进入查询范围
        if request.include_virtual and request.channel_ids:
            for channel_id in request.channel_ids:
                for mapping in self.channel_mappings.get(channel_id, []):
                    extended_channel_ids.update(mapping.get("source_channel_ids", []))

        scoped_channel_ids = list(extended_channel_ids) if extended_channel_ids else None
        
        # 批量查询真实标签在各频道下的聚合结果
        real_tag_rows = await self._get_real_tag_rows(request.guild_id, scoped_channel_ids)

        # 按标签名聚合真实标签与虚拟标签统计
        tag_buckets: Dict[str, Dict[str, Any]] = defaultdict(
            lambda: {"total": 0, "channels": []}
        )

        # 仅把请求范围内的真实标签装入结果桶
        for row_tag_name, row_tag_id, row_channel_id, row_count in real_tag_rows:
            if requested_channels is None or row_channel_id in requested_channels:
                tag_buckets[row_tag_name]["total"] += row_count
                tag_buckets[row_tag_name]["channels"].append(
                    ChannelTagInfo(
                        channel_id=row_channel_id,
                        tag_id=row_tag_id,
                        thread_count=row_count,
                        is_virtual=False,
                    )
                )

        # 按频道映射规则补充虚拟标签统计
        if request.include_virtual:
            await self._append_virtual_tag_stats(tag_buckets, request)

        # 统计当前查询范围内的有效帖子总数
        thread_repository = ThreadRepository(self.session)
        total_threads = await thread_repository.get_total_thread_count_for_scope(
            guild_id=request.guild_id,
            channel_ids=scoped_channel_ids,
        )

        # 构造响应并按帖子数降序排序
        items = [
            TagStatItem(
                tag_name=tag_name,
                total_thread_count=tag_data["total"],
                channel_info=tag_data["channels"],
            )
            for tag_name, tag_data in tag_buckets.items()
            if tag_data["channels"]
        ]
        items.sort(key=lambda item: item.total_thread_count, reverse=True)

        return TagStatsResponse(total_threads=total_threads, items=items)

    async def _get_real_tag_rows(
        self, guild_id: int | None, channel_ids: List[int] | None
    ) -> List[tuple[str, int, int, int]]:
        """查询真实标签在各频道下的聚合统计"""
        tag_id_column = cast(ColumnElement, Tag.id)
        tag_name_column = cast(ColumnElement, Tag.name)
        link_tag_id_column = cast(ColumnElement, ThreadTagLink.tag_id)
        link_thread_id_column = cast(ColumnElement, ThreadTagLink.thread_id)
        thread_id_column = cast(ColumnElement, Thread.id)
        thread_channel_id_column = cast(ColumnElement, Thread.channel_id)
        thread_guild_id_column = cast(ColumnElement, Thread.guild_id)
        thread_not_found_count_column = cast(ColumnElement, Thread.not_found_count)

        # 通过一次跨表聚合查询拿到真实标签统计
        statement: Select = (
            select(
                tag_name_column,
                tag_id_column,
                thread_channel_id_column,
                func.count(func.distinct(thread_id_column)),
            )
            .select_from(Tag)
            .join(ThreadTagLink, tag_id_column == link_tag_id_column)
            .join(Thread, link_thread_id_column == thread_id_column)
            .where(thread_not_found_count_column == 0)
            .group_by(tag_name_column, tag_id_column, thread_channel_id_column)
        )

        if guild_id is not None:
            statement = statement.where(thread_guild_id_column == guild_id)
        if channel_ids:
            statement = statement.where(thread_channel_id_column.in_(channel_ids))

        result = await self.session.execute(statement)
        return [
            (str(row[0]), int(row[1]), int(row[2]), int(row[3]))
            for row in result.all()
        ]

    async def _append_virtual_tag_stats(
        self,
        tag_buckets: Dict[str, Dict[str, Any]],
        request: TagStatsRequest,
    ) -> None:
        """根据频道映射追加虚拟标签统计"""
        thread_repository = ThreadRepository(self.session)
        all_source_ids: set[int] = set()

        # 确定本次需要返回的目标频道集合
        target_channels = (
            request.channel_ids
            if request.channel_ids
            else list(self.channel_mappings.keys())
        )

        # 先收集所有会被虚拟标签引用的源频道
        for target_channel_id in target_channels:
            for mapping in self.channel_mappings.get(target_channel_id, []):
                all_source_ids.update(mapping.get("source_channel_ids", []))

        # 批量查询所有源频道的帖子数
        channel_counts = await thread_repository.get_thread_count_by_channels(
            list(all_source_ids)
        )
        counts_map = {
            item.channel_id: item.thread_count
            for item in channel_counts
        }

        # 逐个目标频道累加其虚拟标签统计
        for target_channel_id in target_channels:
            mappings = self.channel_mappings.get(target_channel_id, [])
            for mapping in mappings:
                tag_name = mapping.get("tag_name")
                source_channel_ids = mapping.get("source_channel_ids", [])

                if not tag_name or not source_channel_ids:
                    continue

                # 虚拟标签值等于所有源频道帖子数之和
                virtual_count = sum(
                    counts_map.get(source_channel_id, 0)
                    for source_channel_id in source_channel_ids
                )
                if virtual_count <= 0:
                    continue

                # 将虚拟标签结果写回聚合桶
                tag_buckets[tag_name]["total"] += virtual_count
                tag_buckets[tag_name]["channels"].append(
                    ChannelTagInfo(
                        channel_id=target_channel_id,
                        tag_id=0,
                        thread_count=virtual_count,
                        is_virtual=True,
                    )
                )
