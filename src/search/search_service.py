import asyncio
import logging
import re
from functools import partial
from typing import Sequence

import rjieba
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload
from sqlmodel import Float, and_, case, cast, func, select

from core.tag_cache_service import TagCacheService
from core.thread_repository import ThreadRepository
from models import Author, Tag, Thread, ThreadTagLink, UserCollection, BooklistItem
from search.qo.cleaned_thread_search import CleanedThreadSearchQuery
from search.qo.thread_search import ThreadSearchQuery
from shared.database import thread_fts_table
from shared.enum.collection_type import CollectionType
from shared.enum.default_preferences import DefaultPreferences
from shared.range_parser import parse_range_string
from shared.time_parser import parse_time_string


class SearchService:
    """封装与搜索相关的数据库操作。"""

    def __init__(self, session: AsyncSession, tag_cache_service: TagCacheService):
        self.session = session
        self.tag_cache_service = tag_cache_service

    def _clean_query(
        self,
        query: ThreadSearchQuery,
        exclude_thread_ids: Sequence[int | str] | None = None,
    ) -> CleanedThreadSearchQuery:
        """
        对 ThreadSearchQuery 进行数据清洗和规范化，返回 CleanedThreadSearchQuery。

        包括：时间解析、标签 ID 解析、排除帖子 ID 规范化、
        包含作者 ID 初始化、作者名规范化。
        """
        # 解析时间字符串
        try:
            created_after_dt = parse_time_string(query.created_after)
            created_before_dt = parse_time_string(query.created_before)
            active_after_dt = parse_time_string(query.active_after)
            active_before_dt = parse_time_string(query.active_before)
        except ValueError as e:
            # 理论上不应该发生，因为在 Modal 中已经验证过
            logging.warning(f"时间字符串解析失败: {e}")
            raise

        # 解析标签 ID
        resolved_include_tag_ids = [
            id
            for name in query.include_tags
            for id in self.tag_cache_service.get_ids_by_tag_name(name)
        ]
        resolved_exclude_tag_ids = [
            id
            for name in query.exclude_tags
            for id in self.tag_cache_service.get_ids_by_tag_name(name)
        ]

        # 规范化排除帖子 ID
        normalized_exclude_thread_ids: list[int] = []
        if exclude_thread_ids:
            for tid in exclude_thread_ids:
                try:
                    normalized_exclude_thread_ids.append(int(tid))
                except (TypeError, ValueError):
                    continue

        # 初始化包含作者 ID
        final_include_author_ids = (
            set(query.include_authors) if query.include_authors else set()
        )

        # 规范化作者名
        normalized_author_name = query.author_name.strip() if query.author_name else None
        if normalized_author_name == "":
            normalized_author_name = None

        return CleanedThreadSearchQuery(
            query=query,
            created_after_dt=created_after_dt,
            created_before_dt=created_before_dt,
            active_after_dt=active_after_dt,
            active_before_dt=active_before_dt,
            resolved_include_tag_ids=resolved_include_tag_ids,
            resolved_exclude_tag_ids=resolved_exclude_tag_ids,
            normalized_exclude_thread_ids=normalized_exclude_thread_ids,
            final_include_author_ids=final_include_author_ids,
            normalized_author_name=normalized_author_name,
        )

    def _apply_range_filter(self, filters, column, range_str):
        """解析范围字符串并应用为SQLAlchemy过滤器"""
        min_val, max_val, min_op, max_op = parse_range_string(range_str)
        if min_val is not None and min_op is not None:
            if min_op == ">=":
                filters.append(column >= min_val)
            else:
                filters.append(column > min_val)

        if max_val is not None and max_op is not None:
            if max_op == "<=":
                filters.append(column <= max_val)
            else:
                filters.append(column < max_val)

    def _apply_ucb1_ranking(
        self,
        statement,
        total_display_count: int,
        exploration_factor: float,
        strength_weight: float,
    ):
        """
        应用 UCB1 算法对帖子进行排序。
        Score = W * (x / n) + C * sqrt(ln(N) / n)
        """
        W = strength_weight
        C = exploration_factor
        N = float(max(1, total_display_count))

        # reaction_count as x
        # display_count as n
        x = cast(Thread.reaction_count, Float)
        n = case(
            (Thread.display_count > 0, cast(Thread.display_count, Float)),
            else_=1.0,  # 避免除零，并给新帖子最大探索加成
        )

        exploitation_term = W * (x / n)
        # N/n 可能会非常大，取对数避免溢出
        exploration_term = C * func.sqrt(func.log(N) / n)

        final_score = (exploitation_term + exploration_term).label("final_score")

        return statement, final_score

    async def search_threads_with_count(
        self,
        query: ThreadSearchQuery,
        *,
        limit: int,
        total_display_count: int,
        exploration_factor: float,
        strength_weight: float,
        offset: int = 0,
        exclude_thread_ids: Sequence[int | str] | None = None,
    ) -> tuple[Sequence[Thread], int]:
        """
        根据搜索条件搜索帖子并分页
        """
        try:
            # 清洗查询数据
            CleanedQo = self._clean_query(query, exclude_thread_ids)

            # --- 步骤 1: 构建过滤器列表 ---
            filters = []

            # 只搜索 not_found_count == 0 的帖子，避免显示被软删除的帖子
            filters.append(Thread.not_found_count == 0)

            # 只搜索 show_flag == True 的帖子，避免显示被隐藏的帖子
            filters.append(Thread.show_flag == True)

            # 当指定了 guild_id 且没有指定具体 channel_ids 时，按服务器过滤
            if CleanedQo.query.guild_id and not CleanedQo.query.channel_ids:
                filters.append(Thread.guild_id == CleanedQo.query.guild_id)
            if CleanedQo.query.channel_ids:
                filters.append(Thread.channel_id.in_(CleanedQo.query.channel_ids)) # type: ignore

            # 处理排除的帖子 ID
            if CleanedQo.normalized_exclude_thread_ids:
                filters.append(~Thread.thread_id.in_(CleanedQo.normalized_exclude_thread_ids)) # type: ignore

            # 处理作者名搜索
            if CleanedQo.normalized_author_name:
                # 查找匹配的作者 ID，以便后续过滤
                search_pattern = f"%{CleanedQo.normalized_author_name}%"

                author_subquery = select(Author.id).where(
                    (func.lower(Author.name) == CleanedQo.normalized_author_name.lower())
                    | (Author.global_name.like(search_pattern)) # type: ignore
                    | (Author.display_name.like(search_pattern)) # type: ignore
                ) # type: ignore

                author_result = await self.session.execute(author_subquery)
                matched_author_ids = set(author_result.scalars().all())

                if CleanedQo.query.include_authors:
                    # 如果同时指定了ID和名称，则取交集
                    CleanedQo.final_include_author_ids.intersection_update(matched_author_ids)
                else:
                    CleanedQo.final_include_author_ids = matched_author_ids

            # 应用作者过滤器
            if CleanedQo.final_include_author_ids:
                filters.append(
                    Thread.author_id.in_(list(CleanedQo.final_include_author_ids)) # type: ignore
                )
            if CleanedQo.query.exclude_authors:
                filters.append(
                    Thread.author_id.notin_(CleanedQo.query.exclude_authors) # type: ignore
                )

            # 反应数范围过滤
            if CleanedQo.query.reaction_count_range != (
                DefaultPreferences.DEFAULT_NUMERIC_RANGE.value
            ):
                self._apply_range_filter(
                    filters, Thread.reaction_count, CleanedQo.query.reaction_count_range
                )

            # 回复数范围过滤
            if CleanedQo.query.reply_count_range != (
                DefaultPreferences.DEFAULT_NUMERIC_RANGE.value
            ):
                self._apply_range_filter(
                    filters, Thread.reply_count, CleanedQo.query.reply_count_range
                )

            # 创建时间范围过滤
            if CleanedQo.created_after_dt:
                filters.append(Thread.created_at >= CleanedQo.created_after_dt)
            if CleanedQo.created_before_dt:
                filters.append(Thread.created_at <= CleanedQo.created_before_dt)

            # 活跃时间范围过滤
            if CleanedQo.active_after_dt or CleanedQo.active_before_dt:
                # 对可能为 None 的 last_active_at 进行安全处理
                conditions = [Thread.last_active_at != None] # noqa: E711
                if CleanedQo.active_after_dt:
                    conditions.append(Thread.last_active_at >= CleanedQo.active_after_dt) # type: ignore
                if CleanedQo.active_before_dt:
                    conditions.append(Thread.last_active_at <= CleanedQo.active_before_dt) # type: ignore
                filters.append(and_(*conditions))

            # 标签过滤
            if CleanedQo.resolved_include_tag_ids:
                if query.tag_logic == "and":
                    # TODO : 考虑精简
                    for tag_name in query.include_tags:
                        ids_for_name = self.tag_cache_service.get_ids_by_tag_name(tag_name)
                        if ids_for_name:
                            filters.append(Thread.tags.any(Tag.id.in_(ids_for_name))) # type: ignore
                else:
                    filters.append(
                        Thread.tags.any(Tag.id.in_(CleanedQo.resolved_include_tag_ids)) # type: ignore
                    )

            if CleanedQo.resolved_exclude_tag_ids:
                filters.append(
                    ~Thread.tags.any(Tag.id.in_(CleanedQo.resolved_exclude_tag_ids)) # type: ignore
                )

            # 关键词匹配过滤
            thread_repo = ThreadRepository(self.session)

            # 处理 FTS 关键词搜索，返回正选的 thread.id 集合和反选的 thread.id 集合。
            fts_result = await thread_repo.get_fts_matched_thread_ids(
                keywords=query.keywords,
                exclude_keywords=query.exclude_keywords,
                exemption_markers=query.exclude_keyword_exemption_markers
            )

            if fts_result.has_include_ids:
                # 有正选关键词
                final_fts_ids = fts_result.get_final_ids()
                if not final_fts_ids:
                    return [], 0

                # 添加过滤条件：帖子 ID 必须在正选关键词（减去排除）的对应 ID 集合中
                filters.append(Thread.id.in_(final_fts_ids))  # type: ignore
            elif fts_result.has_exclude_ids:
                # 没有正选关键词，但有排除关键词：只排除命中排除词的帖子
                filters.append(Thread.id.not_in(fts_result.exclude_ids))  # type: ignore

            # 收藏搜索过滤器
            if query.user_id_for_collection_search:
                # 查询用户的帖子收藏记录，获取该用户收藏过的所有 thread_id
                collected_stmt = select(BooklistItem.thread_id).where(
                    BooklistItem.owner_id == query.user_id_for_collection_search
                ).distinct()

                collected_result = await self.session.execute(collected_stmt)
                collected_thread_ids = list(collected_result.scalars().all())

                if not collected_thread_ids:
                    return [], 0
    
                # 添加过滤条件：帖子 thread_id 必须在该用户的收藏列表中
                filters.append(
                    Thread.thread_id.in_(collected_thread_ids)  # type: ignore
                )

            # --- 步骤 2: 组合所有过滤器 ---

            # 构建基础 SELECT 语句，只查 Thread.id（后续步骤再用 ID 列表查完整数据）
            base_stmt = select(Thread.id).distinct()

            # 将所有已收集的过滤条件用 AND 组合，应用到基础查询语句
            if filters:
                base_stmt = base_stmt.where(and_(*filters))

            # --- 步骤 3: 执行 ID 查询 ---

            # 执行组合了所有过滤条件的查询，获取满足所有条件的帖子 ID 列表
            id_result = await self.session.execute(base_stmt)
            matched_ids = list(id_result.scalars().all())

            # matched_ids 的长度就是满足所有条件的帖子总数，用于分页计算
            total_count = len(matched_ids)

            if total_count == 0:
                return [], 0

            # --- 步骤 4: 用满足条件的帖子 ID 列表进行排序，并获取返回分页的关联数据 ---
            final_select_stmt = (
                select(Thread)
                .where(Thread.id.in_(matched_ids))  # type: ignore
                .options(
                    selectinload(Thread.tags),  # type: ignore
                    joinedload(Thread.author),  # type: ignore
                )
            )

            order_by = None

            # 如果是自定义搜索，则使用其基础排序算法，否则使用主排序算法
            effective_sort_method = (
                query.custom_base_sort
                if query.sort_method == "custom"
                else query.sort_method
            )

            # 如果按收藏时间排序，但不是收藏搜索，则退回综合排序
            if (
                effective_sort_method == "collected_at"
                and not query.user_id_for_collection_search
            ):
                effective_sort_method = "comprehensive"

            if effective_sort_method == "comprehensive":
                # 按综合排序
                final_select_stmt, final_score_expr = self._apply_ucb1_ranking(
                    final_select_stmt,
                    total_display_count,
                    exploration_factor,
                    strength_weight,
                )
                order_by = (
                    final_score_expr.desc()
                    if query.sort_order == "desc"
                    else final_score_expr.asc()
                )
            elif (
                effective_sort_method == "collected_at"
                and query.user_id_for_collection_search
            ):
                # 按收藏时间排序
                final_select_stmt = final_select_stmt.join(
                    BooklistItem,
                    and_(
                        Thread.thread_id == BooklistItem.thread_id,
                        BooklistItem.owner_id == query.user_id_for_collection_search,
                    ),
                ).group_by(Thread.id) # type: ignore

                # 取最新的收藏时间
                sort_col = func.max(BooklistItem.created_at) 
                order_by = (
                    sort_col.desc() if query.sort_order == "desc" else sort_col.asc()
                )
            else:
                # 按对应统计数据排序
                sort_col_name = (
                    effective_sort_method
                    if hasattr(Thread, effective_sort_method)
                    else "last_active_at"
                )
                sort_col = getattr(Thread, sort_col_name)
                order_by = (
                    sort_col.desc() if query.sort_order == "desc" else sort_col.asc()
                )

            # 应用排序
            if order_by is not None:
                final_select_stmt = final_select_stmt.order_by(order_by)

            # 应用偏移和返回数
            if offset:
                final_select_stmt = final_select_stmt.offset(offset)
            final_select_stmt = final_select_stmt.limit(limit)

            result = await self.session.execute(final_select_stmt)
            threads = result.scalars().all()

            return threads, total_count

        except Exception:
            logging.error(
                "Error during search_threads_with_count execution", exc_info=True
            )
            raise

    async def get_tags_for_author(self, author_id: int) -> Sequence[Tag]:
        """获取指定作者发布过的所有帖子的唯一标签列表"""
        statement = (
            select(Tag)
            .join(Thread, Tag.threads)  # type: ignore
            .where(Thread.author_id == author_id)  # type: ignore
            .distinct()
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_thread_by_discord_id(self, discord_thread_id: int) -> Thread | None:
        """按 Discord thread_id 取单帖（含标签与作者），仅返回仍参与搜索的帖子。"""
        statement = (
            select(Thread)
            .where(
                Thread.thread_id == discord_thread_id,  # type: ignore[arg-type]
                Thread.not_found_count == 0,
            )
            .options(
                selectinload(Thread.tags),  # type: ignore
                joinedload(Thread.author),  # type: ignore
            )
        )
        result = await self.session.execute(statement)
        return result.scalars().first()

    async def get_tags_for_collections(self, user_id: int) -> Sequence[Tag]:
        """获取指定用户收藏的所有帖子的唯一标签列表"""

        statement = (
            select(Tag)
            .join(ThreadTagLink, Tag.id == ThreadTagLink.tag_id)  # type: ignore
            .join(Thread, ThreadTagLink.thread_id == Thread.id)  # type: ignore
            .join(
                BooklistItem,
                Thread.thread_id == BooklistItem.thread_id,  # type: ignore
            )
            .where(BooklistItem.owner_id == user_id)
            .distinct()
        )
        result = await self.session.execute(statement)
        return result.scalars().all()
