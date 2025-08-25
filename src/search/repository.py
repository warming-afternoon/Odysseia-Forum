import logging
import re
from typing import Sequence
from sqlmodel import select, func, and_, case, cast, Float, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.database import thread_fts_table
from shared.models.thread_tag_link import ThreadTagLink
from shared.models.thread import Thread
from shared.models.tag import Tag
from shared.models.user_search_preferences import UserSearchPreferences
from .dto.user_search_preferences import UserSearchPreferencesDTO
from search.qo.thread_search import ThreadSearchQuery
from shared.ranking_config import RankingConfig
from tag_system.tagService import TagService


class SearchRepository:
    """封装与搜索相关的数据库操作。"""

    def __init__(self, session: AsyncSession, tag_service: TagService):
        self.session = session
        self.tag_service = tag_service

    def _apply_search_filters(
        self, query, statement, resolved_include_tag_ids, resolved_exclude_tag_ids
    ):
        """将搜索查询对象中的过滤条件应用到查询语句上"""
        if query.channel_ids:
            statement = statement.where(Thread.channel_id.in_(query.channel_ids))
        if query.include_authors:
            statement = statement.where(Thread.author_id.in_(query.include_authors))
        if query.exclude_authors:
            statement = statement.where(Thread.author_id.notin_(query.exclude_authors))
        if query.after_ts:
            statement = statement.where(Thread.created_at >= query.after_ts)
        if query.before_ts:
            statement = statement.where(Thread.created_at <= query.before_ts)
        if resolved_include_tag_ids:
            if query.tag_logic == "and":
                for tag_name in query.include_tags:
                    ids_for_name = self.tag_service.get_ids_by_name(tag_name)
                    if ids_for_name:
                        statement = statement.where(
                            Thread.tags.any(Tag.id.in_(ids_for_name))
                        )
            else:
                statement = statement.where(
                    Thread.tags.any(Tag.id.in_(resolved_include_tag_ids))
                )
        if resolved_exclude_tag_ids:
            statement = statement.where(
                ~Thread.tags.any(Tag.id.in_(resolved_exclude_tag_ids))
            )
        return statement

    def _apply_ranking(self, statement, resolved_include_tag_ids):
        if resolved_include_tag_ids:
            statement = statement.outerjoin(
                ThreadTagLink,
                and_(
                    Thread.id == ThreadTagLink.thread_id,
                    ThreadTagLink.tag_id.in_(resolved_include_tag_ids),
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
        self, query: ThreadSearchQuery, offset: int, limit: int, **kwargs
    ) -> tuple[Sequence[Thread], int]:
        """
        根据搜索条件搜索帖子并分页。
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

            # --- 步骤 1: 获取候选ID集合 (非FTS过滤) ---
            candidate_ids_stmt = select(Thread.id).where(Thread.id.isnot(None))
            candidate_ids_stmt = self._apply_search_filters(
                query,
                candidate_ids_stmt,
                resolved_include_tag_ids,
                resolved_exclude_tag_ids,
            )
            result = await self.session.execute(candidate_ids_stmt)
            final_ids = set(result.scalars().all())

            if not final_ids:
                return [], 0

            # --- 步骤 2: 正选关键词交集 ---
            if query.keywords:
                fts_filters = []
                keywords_str = query.keywords.replace("，", ",").replace("／", "/")
                and_groups = [
                    group.strip() for group in keywords_str.split(",") if group.strip()
                ]
                for group in and_groups:
                    or_keywords = [
                        f'"{kw.strip()}"' for kw in group.split("/") if kw.strip()
                    ]
                    if or_keywords:
                        fts_filters.append(
                            thread_fts_table.c.title.op("MATCH")(" OR ".join(or_keywords))
                        )

                if fts_filters:
                    include_stmt = select(thread_fts_table.c.rowid).where(
                        and_(*fts_filters)
                    )
                    result = await self.session.execute(include_stmt)
                    include_ids = set(result.scalars().all())
                    final_ids.intersection_update(include_ids)

            if not final_ids:
                return [], 0

            # --- 步骤 3: 反选关键词差集---
            if query.exclude_keywords:
                exemption_markers = query.exclude_keyword_exemption_markers or [
                    "禁",
                    "🈲",
                ]
                exclude_keywords_list = [
                    kw.strip()
                    for kw in re.split(r"[,，/\s]+", query.exclude_keywords)
                    if kw.strip()
                ]

                # VITAL PERFORMANCE FIX: Build a single MATCH query for all excluded keywords
                all_exclude_parts = []
                for keyword in exclude_keywords_list:
                    exemption_clauses = [
                        f'NEAR("{keyword}" "{marker}", 5)'
                        for marker in exemption_markers
                    ]
                    exemption_match_str = f"({' OR '.join(exemption_clauses)})"
                    # Each part is a complete exclusion expression for one keyword
                    all_exclude_parts.append(f'"{keyword}" NOT {exemption_match_str}')

                if all_exclude_parts:
                    # 用 OR 链接
                    final_exclude_expr = " OR ".join(all_exclude_parts)
                    exclude_stmt = select(thread_fts_table.c.rowid).where(
                        thread_fts_table.c.title.op("MATCH")(final_exclude_expr)
                    )
                    result = await self.session.execute(exclude_stmt)
                    exclude_ids = set(result.scalars().all())
                    final_ids.difference_update(exclude_ids)

            if not final_ids:
                return [], 0

            # --- 步骤 4: 计数 ---
            total_count = len(final_ids)

            # --- 步骤 5: 获取分页数据 ---
            if not final_ids:
                return [], total_count

            final_ids_list = list(final_ids)

            statement_to_execute = select(Thread).where(Thread.id.in_(final_ids_list))

            order_by = None
            if query.sort_method == "comprehensive":
                statement_to_execute, final_score_expr = self._apply_ranking(
                    statement_to_execute, resolved_include_tag_ids
                )
                order_by = (
                    final_score_expr.desc()
                    if query.sort_order == "desc"
                    else final_score_expr.asc()
                )
            else:
                sort_col_name = (
                    query.sort_method
                    if hasattr(Thread, query.sort_method)
                    else "last_active_at"
                )
                sort_col = getattr(Thread, sort_col_name)
                order_by = (
                    sort_col.desc() if query.sort_order == "desc" else sort_col.asc()
                )

            if order_by is not None:
                statement_to_execute = statement_to_execute.order_by(order_by)

            statement_to_execute = (
                statement_to_execute.options(selectinload(Thread.tags))  # type: ignore
                .offset(offset)
                .limit(limit)
            )

            result = await self.session.execute(statement_to_execute)
            threads = result.scalars().all()
            return threads, total_count

        except Exception:
            logging.error(
                "Error during search_threads_with_count execution", exc_info=True
            )
            raise

    async def get_user_preferences(self, user_id, **kwargs):
        """获取用户的搜索偏好设置"""
        prefs_orm = await self.session.get(UserSearchPreferences, user_id)
        if not prefs_orm:
            return None
        return UserSearchPreferencesDTO.model_validate(prefs_orm)

    async def save_user_preferences(self, user_id, prefs_data, **kwargs):
        """创建或更新用户的搜索偏好设置"""
        prefs = await self.session.get(UserSearchPreferences, user_id)
        if prefs:
            for key, value in prefs_data.items():
                setattr(prefs, key, value)
        else:
            prefs = UserSearchPreferences(user_id=user_id, **prefs_data)
        self.session.add(prefs)
        await self.session.commit()
        await self.session.refresh(prefs)
        return UserSearchPreferencesDTO.model_validate(prefs)

    async def get_tags_for_author(self, author_id, **kwargs):
        """获取指定作者使用过的所有标签"""
        statement = (
            select(Tag)
            .join(Thread.tags)  # type: ignore
            .where(Thread.author_id == author_id)
            .distinct()
        )
        result = await self.session.execute(statement)
        return result.scalars().all()
