import logging
from typing import List, Optional, Sequence
from sqlmodel import select, func, or_, and_, case, cast, Float
from sqlalchemy.sql import true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.models.thread_tag_link import ThreadTagLink
from shared.models.thread import Thread
from shared.models.tag import Tag
from shared.models.user_search_preferences import UserSearchPreferences
from .dto.user_search_preferences import UserSearchPreferencesDTO
from search.qo.thread_search import ThreadSearchQuery
from ranking_config import RankingConfig
from tag_system.tagService import TagService


class SearchRepository:
    """封装与搜索相关的数据库操作。"""

    def __init__(self, session: AsyncSession, tag_service: TagService):
        self.session = session
        self.tag_service = tag_service

    def _apply_search_filters(
        self,
        query: ThreadSearchQuery,
        statement,
        resolved_include_tag_ids: List[int],
        resolved_exclude_tag_ids: List[int],
    ):
        """将搜索查询对象中的过滤条件应用到查询语句上"""
        if query.channel_ids:
            statement = statement.where(Thread.channel_id.in_(query.channel_ids))  # type: ignore

        if query.include_authors:
            statement = statement.where(Thread.author_id.in_(query.include_authors))  # type: ignore
        if query.exclude_authors:
            statement = statement.where(Thread.author_id.notin_(query.exclude_authors))  # type: ignore

        if query.after_ts:
            statement = statement.where(Thread.created_at >= query.after_ts)
        if query.before_ts:
            statement = statement.where(Thread.created_at <= query.before_ts)

        if resolved_include_tag_ids:
            if query.tag_logic == "and":
                for tag_name in query.include_tags:
                    # 获取该名称对应的所有ID
                    ids_for_name = self.tag_service.get_ids_by_name(tag_name)
                    if ids_for_name:
                        # 帖子必须至少有这些ID中的一个
                        statement = statement.where(
                            Thread.tags.any(Tag.id.in_(ids_for_name))  # type: ignore
                        )
            else:  # OR 逻辑
                statement = statement.where(
                    Thread.tags.any(Tag.id.in_(resolved_include_tag_ids))  # type: ignore
                )

        if resolved_exclude_tag_ids:
            statement = statement.where(
                ~Thread.tags.any(Tag.id.in_(resolved_exclude_tag_ids))  # type: ignore
            )

        if query.keywords:
            # 替换中文逗号和斜杠
            keywords_str = query.keywords.replace("，", ",").replace("／", "/")

            # 按逗号分割成 AND 组
            and_groups = [
                group.strip() for group in keywords_str.split(",") if group.strip()
            ]

            final_keyword_clause = []
            for group in and_groups:
                # 按斜杠分割成 OR 组
                or_keywords = [kw.strip() for kw in group.split("/") if kw.strip()]

                or_clauses = []
                for keyword in or_keywords:
                    or_clauses.append(Thread.title.ilike(f"%{keyword}%"))  # type: ignore
                    or_clauses.append(
                        Thread.first_message_excerpt.ilike(f"%{keyword}%")  # type: ignore
                    )

                if or_clauses:
                    final_keyword_clause.append(or_(*or_clauses))

            if final_keyword_clause:
                statement = statement.where(and_(*final_keyword_clause))

        if query.exclude_keywords:
            # 排除关键词使用 OR 逻辑：包含任何一个排除关键词就排除该帖子
            exclude_keywords_str = query.exclude_keywords.replace("，", ",").replace(
                "／", "/"
            )
            exclude_keywords_list = [
                kw.strip() for kw in exclude_keywords_str.split(",") if kw.strip()
            ]

            for keyword in exclude_keywords_list:
                statement = statement.where(~Thread.title.ilike(f"%{keyword}%"))  # type: ignore
                statement = statement.where(
                    ~Thread.first_message_excerpt.ilike(f"%{keyword}%")  # type: ignore
                )

        return statement

    def _apply_ranking(self, statement, resolved_include_tag_ids: List[int]):
        """应用综合排序逻辑"""

        # 优先 LEFT JOIN 到 ThreadTagLink 表
        if resolved_include_tag_ids:
            statement = statement.outerjoin(
                ThreadTagLink,
                and_(
                    Thread.id == ThreadTagLink.thread_id,
                    ThreadTagLink.tag_id.in_(resolved_include_tag_ids),  # type: ignore
                ),
            )

            # 直接从 JOIN 的表中提取投票数据
            upvotes_expr = func.coalesce(cast(ThreadTagLink.upvotes, Float), 0.0)
            downvotes_expr = func.coalesce(cast(ThreadTagLink.downvotes, Float), 0.0)

            total_votes = upvotes_expr + downvotes_expr

            # Wilson Score 计算
            z = RankingConfig.WILSON_CONFIDENCE_LEVEL
            z_squared = z * z  
            p_hat = case((total_votes > 0, upvotes_expr / total_votes), else_=0.0)
            # 威尔逊得分下限公式
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
            # 如果没有指定标签，则赋予默认分，且不进行 JOIN
            tag_weight = cast(RankingConfig.DEFAULT_TAG_SCORE, Float)
            total_votes = cast(0, Float)

        # 时间和反应权重计算
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
        根据搜索条件搜索帖子并分页，同时返回结果总数。
        """
        try:
            # 1. 解析标签名称为ID
            resolved_include_tag_ids = []
            if query.include_tags:
                for name in query.include_tags:
                    resolved_include_tag_ids.extend(
                        self.tag_service.get_ids_by_name(name)
                    )

            resolved_exclude_tag_ids = []
            if query.exclude_tags:
                for name in query.exclude_tags:
                    resolved_exclude_tag_ids.extend(
                        self.tag_service.get_ids_by_name(name)
                    )

            # 2. 构建查询
            count_cte = self._apply_search_filters(
                query,
                select(func.count(Thread.id).label("total_count")),  # type: ignore
                resolved_include_tag_ids,
                resolved_exclude_tag_ids,
            ).cte("count_cte")

            base_select = select(Thread, count_cte.c.total_count).join(
                count_cte, true()
            )
            statement = self._apply_search_filters(
                query, base_select, resolved_include_tag_ids, resolved_exclude_tag_ids
            )

            # 3. 应用排序
            order_by = None
            if query.sort_method == "comprehensive":
                statement, final_score_expr = self._apply_ranking(
                    statement, resolved_include_tag_ids
                )
                order_by = (
                    final_score_expr.desc()
                    if query.sort_order == "desc"
                    else final_score_expr.asc()
                )
            elif query.sort_method == "created_time":
                order_by = (
                    Thread.created_at.desc()  # type: ignore
                    if query.sort_order == "desc"
                    else Thread.created_at.asc()  # type: ignore
                )
            elif query.sort_method == "active_time":
                order_by = (
                    Thread.last_active_at.desc()  # type: ignore
                    if query.sort_order == "desc"
                    else Thread.last_active_at.asc()  # type: ignore
                )
            elif query.sort_method == "reaction_count":
                order_by = (
                    Thread.reaction_count.desc()  # type: ignore
                    if query.sort_order == "desc"
                    else Thread.reaction_count.asc()  # type: ignore
                )
            else:
                order_by = (
                    Thread.last_active_at.desc()  # type: ignore
                    if query.sort_order == "desc"
                    else Thread.last_active_at.asc()  # type: ignore
                )

            if order_by is not None:
                statement = statement.order_by(order_by)

            statement = (
                statement.options(selectinload(Thread.tags))  # type: ignore
                .offset(offset)
                .limit(limit)
            )

            # logging.info(f"Executing search query: {statement.compile(compile_kwargs={'literal_binds': True})}")
            result = await self.session.execute(statement)

            rows = result.all()
            if not rows:
                return [], 0

            # 日志记录

            threads = [row.Thread for row in rows]
            total_count = rows[0].total_count if rows else 0

            return threads, total_count

        except Exception:
            logging.error(
                "Error during search_threads_with_count execution", exc_info=True
            )
            raise

    async def get_user_preferences(
        self, user_id: int
    ) -> Optional[UserSearchPreferencesDTO]:
        """获取用户的搜索偏好设置"""
        prefs_orm = await self.session.get(UserSearchPreferences, user_id)
        if not prefs_orm:
            return None
        return UserSearchPreferencesDTO.model_validate(prefs_orm)

    async def save_user_preferences(
        self, user_id: int, prefs_data: dict
    ) -> UserSearchPreferencesDTO:
        """创建或更新用户的搜索偏好设置。"""
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

    async def get_tags_for_author(self, author_id: int) -> Sequence[Tag]:
        """获取指定作者使用过的所有标签。"""
        statement = (
            select(Tag)
            .join(Thread.tags)  # type: ignore
            .where(Thread.author_id == author_id)
            .distinct()
        )
        result = await self.session.execute(statement)
        return result.scalars().all()
