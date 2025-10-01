import logging
import re
from typing import Sequence
from sqlmodel import select, func, and_, case, cast, Float
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.database import thread_fts_table
from shared.models.thread_tag_link import ThreadTagLink
from shared.models.thread import Thread
from shared.models.tag import Tag
from search.qo.thread_search import ThreadSearchQuery
from shared.ranking_config import RankingConfig
from core.tagService import TagService
from shared.range_parser import parse_range_string
from shared.default_preferences import DefaultPreferences


class SearchRepository:
    """封装与搜索相关的数据库操作。"""

    def __init__(self, session: AsyncSession, tag_service: TagService):
        self.session = session
        self.tag_service = tag_service

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

    def _apply_ranking(self, statement, resolved_include_tag_ids):
        if resolved_include_tag_ids:
            statement = statement.outerjoin(
                ThreadTagLink,
                and_(
                    Thread.id == ThreadTagLink.thread_id,
                    ThreadTagLink.tag_id.in_(resolved_include_tag_ids),  # type: ignore
                ),
            )
            upvotes_expr = func.coalesce(cast(ThreadTagLink.upvotes, Float), 0.0)
            downvotes_expr = func.coalesce(cast(ThreadTagLink.downvotes, Float), 0.0)
            total_votes = upvotes_expr + downvotes_expr
            z = RankingConfig.WILSON_CONFIDENCE_LEVEL
            z_squared = z * z
            p_hat = case((total_votes > 0, upvotes_expr / total_votes), else_=0.0)
            wilson_score_expr = case(
                (
                    total_votes > 0,
                    (
                        p_hat
                        + z_squared / (2 * total_votes)
                        - z
                        * func.sqrt(
                            (p_hat * (1 - p_hat) + z_squared / (4 * total_votes))
                            / total_votes
                        )
                    )
                    / (1 + z_squared / total_votes),
                ),
                else_=0.0,
            )
            tag_weight = case(
                (total_votes > 0, wilson_score_expr),
                else_=RankingConfig.DEFAULT_TAG_SCORE,
            )
        else:
            tag_weight = cast(RankingConfig.DEFAULT_TAG_SCORE, Float)
            total_votes = cast(0, Float)
        time_diff_days = func.julianday("now") - func.julianday(Thread.last_active_at)
        time_weight = func.exp(-RankingConfig.TIME_DECAY_RATE * time_diff_days)
        reaction_weight = func.min(
            RankingConfig.MAX_REACTION_SCORE,
            func.log(cast(Thread.reaction_count, Float) + 1)
            / func.log(RankingConfig.REACTION_LOG_BASE + 1),
        ).label("reaction_weight")
        base_score = (
            time_weight * RankingConfig.TIME_WEIGHT_FACTOR
            + tag_weight * RankingConfig.TAG_WEIGHT_FACTOR
            + reaction_weight * RankingConfig.REACTION_WEIGHT_FACTOR
        ).label("base_score")
        penalty_factor = case(
            (
                and_(
                    tag_weight < RankingConfig.SEVERE_PENALTY_THRESHOLD,
                    total_votes >= RankingConfig.SEVERE_PENALTY_MIN_VOTES,
                ),
                RankingConfig.SEVERE_PENALTY_FACTOR,
            ),
            (
                and_(
                    tag_weight < RankingConfig.MILD_PENALTY_THRESHOLD,
                    total_votes >= RankingConfig.MILD_PENALTY_MIN_VOTES,
                ),
                RankingConfig.MILD_PENALTY_FACTOR,
            ),
            else_=1.0,
        ).label("penalty_factor")
        final_score = (base_score * penalty_factor).label("final_score")
        return statement, final_score

    async def search_threads_with_count(
        self, query: ThreadSearchQuery, offset: int, limit: int
    ) -> tuple[Sequence[Thread], int]:
        """
        根据搜索条件搜索帖子并分页
        """
        try:
            # --- 步骤 0: 解析标签 ---
            resolved_include_tag_ids = [
                id
                for name in query.include_tags
                for id in self.tag_service.get_ids_by_name(name)
            ]
            resolved_exclude_tag_ids = [
                id
                for name in query.exclude_tags
                for id in self.tag_service.get_ids_by_name(name)
            ]

            # --- 步骤 1: 构建基础过滤器列表 (除了反选关键词) ---
            filters = []
            # -- 标准字段过滤 --
            # 只搜索 not_found_count == 0 的帖子，避免显示被软删除的帖子
            filters.append(Thread.not_found_count == 0)
            if query.channel_ids:
                filters.append(Thread.channel_id.in_(query.channel_ids))  # type: ignore
            if query.include_authors:
                filters.append(Thread.author_id.in_(query.include_authors))  # type: ignore
            if query.exclude_authors:
                filters.append(Thread.author_id.notin_(query.exclude_authors))  # type: ignore
            if query.after_ts:
                filters.append(Thread.created_at >= query.after_ts)
            if query.before_ts:
                filters.append(Thread.created_at <= query.before_ts)

            # --- 自定义排序算法的范围过滤---
            if query.reaction_count_range != DefaultPreferences.DEFAULT_NUMERIC_RANGE.value:
                self._apply_range_filter(filters, Thread.reaction_count, query.reaction_count_range)
            if query.reply_count_range != DefaultPreferences.DEFAULT_NUMERIC_RANGE.value:
                self._apply_range_filter(filters, Thread.reply_count, query.reply_count_range)

            if query.created_after:
                filters.append(Thread.created_at >= query.created_after)
            if query.created_before:
                filters.append(Thread.created_at <= query.created_before)
            
            # 对可能为 None 的 last_active_at 进行安全处理
            if query.active_after or query.active_before:
                conditions = [Thread.last_active_at != None]
                if query.active_after:
                    conditions.append(Thread.last_active_at >= query.active_after)  # type: ignore
                if query.active_before:
                    conditions.append(Thread.last_active_at <= query.active_before)  # type: ignore
                filters.append(and_(*conditions))

            # -- 标签过滤 --
            if resolved_include_tag_ids:
                if query.tag_logic == "and":
                    for tag_name in query.include_tags:
                        ids_for_name = self.tag_service.get_ids_by_name(tag_name)
                        if ids_for_name:
                            filters.append(Thread.tags.any(Tag.id.in_(ids_for_name)))  # type: ignore
                else:
                    filters.append(
                        Thread.tags.any(Tag.id.in_(resolved_include_tag_ids))  # type: ignore
                    )  # type: ignore
            if resolved_exclude_tag_ids:
                filters.append(~Thread.tags.any(Tag.id.in_(resolved_exclude_tag_ids)))  # type: ignore

            # --- 步骤 2: 单独处理反选关键词 ---
            if query.exclude_keywords:
                exemption_markers = (
                    query.exclude_keyword_exemption_markers
                    if query.exclude_keyword_exemption_markers is not None
                    else ["禁", "🈲"]
                )
                exclude_keywords_list = [
                    kw.strip()
                    for kw in re.split(r"[,，/\s]+", query.exclude_keywords)
                    if kw.strip()
                ]

                all_exclude_parts = []
                for keyword in exclude_keywords_list:
                    # 只有在豁免标记列表非空时才构建豁免逻辑
                    if exemption_markers:
                        exemption_clauses = [
                            f'NEAR("{keyword}" "{marker}", 8)'
                            for marker in exemption_markers
                        ]
                        exemption_match_str = f"({' OR '.join(exemption_clauses)})"
                        # 构建带有 NOT 的 FTS 表达式
                        all_exclude_parts.append(
                            f'"{keyword}" NOT {exemption_match_str}'
                        )
                    else:
                        # 如果没有豁免标记，直接排除关键词
                        all_exclude_parts.append(f'"{keyword}"')

                if all_exclude_parts:
                    final_exclude_expr = " OR ".join(all_exclude_parts)
                    # 创建一个子查询，专门用于找出要排除的 thread ID
                    exclude_ids_subquery = select(thread_fts_table.c.rowid).where(
                        thread_fts_table.c.thread_fts.op("MATCH")(final_exclude_expr)
                    )
                    # 将排除逻辑添加到主过滤器中
                    filters.append(Thread.id.not_in(exclude_ids_subquery))  # type: ignore

            # --- 步骤 3: 组合正选关键词和其他过滤器 ---
            base_stmt = select(Thread.id).distinct()
            needs_fts_join = query.keywords  # 只在有正选关键词时才需要JOIN
            if needs_fts_join:
                base_stmt = base_stmt.join(
                    thread_fts_table,
                    Thread.id == thread_fts_table.c.rowid,  # type: ignore
                )

            # -- 正选关键词 --
            if query.keywords:
                keywords_str = query.keywords.replace("，", ",").replace("／", "/")
                and_groups = [
                    group.strip() for group in keywords_str.split(",") if group.strip()
                ]
                for group in and_groups:
                    or_keywords = [
                        f'"{kw.strip()}"' for kw in group.split("/") if kw.strip()
                    ]
                    if or_keywords:
                        filters.append(
                            thread_fts_table.c.thread_fts.op("MATCH")(
                                " OR ".join(or_keywords)
                            )
                        )

            # 应用所有过滤器
            if filters:
                base_stmt = base_stmt.where(and_(*filters))

            # --- 步骤 4: 计数 ---
            count_stmt = select(func.count()).select_from(base_stmt.alias("sub"))
            count_result = await self.session.execute(count_stmt)
            total_count = count_result.scalar_one_or_none() or 0

            if total_count == 0:
                return [], 0

            # --- 步骤 5: 获取分页数据和排序 ---
            final_select_stmt = select(Thread).where(Thread.id.in_(base_stmt))  # type: ignore

            order_by = None
            
            # --- 修改：确定排序方法 ---
            # 如果是自定义搜索，则使用其基础排序算法，否则使用主排序算法
            effective_sort_method = query.custom_base_sort if query.sort_method == "custom" else query.sort_method
            
            if effective_sort_method == "comprehensive":
                final_select_stmt, final_score_expr = self._apply_ranking(
                    final_select_stmt, resolved_include_tag_ids
                )
                order_by = (
                    final_score_expr.desc()
                    if query.sort_order == "desc"
                    else final_score_expr.asc()
                )
            else:
                sort_col_name = (
                    effective_sort_method
                    if hasattr(Thread, effective_sort_method)
                    else "last_active_at"
                )
                sort_col = getattr(Thread, sort_col_name)
                order_by = (
                    sort_col.desc() if query.sort_order == "desc" else sort_col.asc()
                )
            # --- 结束修改 ---

            if order_by is not None:
                final_select_stmt = final_select_stmt.order_by(order_by)

            final_select_stmt = (
                final_select_stmt.options(selectinload(Thread.tags))  # type: ignore
                .offset(offset)
                .limit(limit)
            )

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
