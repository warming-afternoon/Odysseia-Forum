from typing import List, Optional, Sequence
from sqlmodel import select, func, or_, case, cast, JSON, Float
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from shared.models.thread import Thread
from shared.models.tag import Tag
from shared.models.user_search_preferences import UserSearchPreferences
from search.models.qo.thread_search import ThreadSearchQuery
from ranking_config import RankingConfig

class SearchRepository:
    """封装与搜索相关的数据库操作。"""

    def __init__(self, session: AsyncSession):
        self.session = session

    def _apply_search_filters(self, query: ThreadSearchQuery, statement):
        """将搜索查询对象中的过滤条件应用到查询语句上"""
        if query.channel_ids:
            statement = statement.where(Thread.channel_id.in_(query.channel_ids))
        
        if query.include_authors:
            statement = statement.where(Thread.author_id.in_(query.include_authors))
        if query.exclude_authors:
            statement = statement.where(Thread.author_id.notin_(query.exclude_authors))

        if query.after_date:
            statement = statement.where(Thread.created_at >= query.after_date)
        if query.before_date:
            statement = statement.where(Thread.created_at <= query.before_date)

        if query.include_tags:
            if query.tag_logic == 'and':
                for tag_name in query.include_tags:
                    statement = statement.where(Thread.tags.any(Tag.name == tag_name))
            else: # or
                statement = statement.where(Thread.tags.any(Tag.name.in_(query.include_tags)))
        
        if query.exclude_tags:
            statement = statement.where(~Thread.tags.any(Tag.name.in_(query.exclude_tags)))

        if query.keywords:
            # 替换中文逗号和斜杠
            keywords_str = query.keywords.replace('，', ',').replace('／', '/')
            
            # 按逗号分割成 AND 组
            and_groups = [group.strip() for group in keywords_str.split(',') if group.strip()]
            
            final_keyword_clause = []
            for group in and_groups:
                # 按斜杠分割成 OR 组
                or_keywords = [kw.strip() for kw in group.split('/') if kw.strip()]
                
                or_clauses = []
                for keyword in or_keywords:
                    or_clauses.append(Thread.title.ilike(f"%{keyword}%"))
                    or_clauses.append(Thread.first_message_excerpt.ilike(f"%{keyword}%"))
                
                if or_clauses:
                    final_keyword_clause.append(or_(*or_clauses))
            
            if final_keyword_clause:
                statement = statement.where(and_(*final_keyword_clause))

        if query.exclude_keywords:
            # 排除关键词使用 OR 逻辑：包含任何一个排除关键词就排除该帖子
            exclude_keywords_str = query.exclude_keywords.replace('，', ',').replace('／', '/')
            exclude_keywords_list = [kw.strip() for kw in exclude_keywords_str.split(',') if kw.strip()]
            
            for keyword in exclude_keywords_list:
                statement = statement.where(~Thread.title.ilike(f"%{keyword}%"))
                statement = statement.where(~Thread.first_message_excerpt.ilike(f"%{keyword}%"))

        return statement

    async def count_threads(self, query: ThreadSearchQuery) -> int:
        """根据搜索条件统计帖子总数"""
        statement = select(func.count(Thread.id))
        statement = self._apply_search_filters(query, statement)
        
        result = await self.session.execute(statement)
        return result.one()

    async def search_threads(self, query: ThreadSearchQuery, offset: int, limit: int) -> Sequence[Thread]:
        """根据搜索条件搜索帖子并分页"""
        statement = select(Thread).options(selectinload(Thread.tags))
        statement = self._apply_search_filters(query, statement)

        # 排序
        if query.sort_method == 'comprehensive':
            # --- 1. 计算标签总分 (Wilson Score) ---
            total_upvotes_expr = 0.0
            total_downvotes_expr = 0.0
            if query.include_tags:
                for tag_name in query.include_tags:
                    total_upvotes_expr += func.coalesce(cast(func.json_extract(Thread.tag_votes_summary, f'$.{tag_name}.upvotes'), Float), 0.0)
                    total_downvotes_expr += func.coalesce(cast(func.json_extract(Thread.tag_votes_summary, f'$.{tag_name}.downvotes'), Float), 0.0)
            
            total_votes = total_upvotes_expr + total_downvotes_expr
            
            # Wilson Score Lower Bound 实现
            z = RankingConfig.WILSON_CONFIDENCE_LEVEL
            
            # 使用 case 表达式避免除以零
            tag_weight = case(
                (total_votes > 0,
                    (
                        (total_upvotes_expr / total_votes + z*z / (2 * total_votes)) -
                        z * func.sqrt((total_upvotes_expr * total_downvotes_expr) / total_votes + z*z / (4 * total_votes)) / total_votes
                    ) / (1 + z*z / total_votes)
                ),
                else_ = RankingConfig.DEFAULT_TAG_SCORE
            )

            # --- 2. 计算时间权重 (指数衰减) ---
            # julianday 计算天数差，更精确
            time_diff_days = func.julianday('now') - func.julianday(Thread.last_active_at)
            time_weight = func.exp(-RankingConfig.TIME_DECAY_RATE * time_diff_days)

            # --- 3. 计算反应权重 (对数归一化) ---
            reaction_weight = func.min(
                RankingConfig.MAX_REACTION_SCORE,
                func.log(cast(Thread.reaction_count, Float) + 1) / func.log(RankingConfig.REACTION_LOG_BASE + 1)
            )

            # --- 4. 计算基础综合分数 ---
            base_score = (
                time_weight * RankingConfig.TIME_WEIGHT_FACTOR +
                tag_weight * RankingConfig.TAG_WEIGHT_FACTOR +
                reaction_weight * RankingConfig.REACTION_WEIGHT_FACTOR
            )

            # --- 5. 应用恶评惩罚 ---
            final_score = case(
                (
                    (tag_weight < RankingConfig.SEVERE_PENALTY_THRESHOLD) &
                    (total_votes >= RankingConfig.SEVERE_PENALTY_MIN_VOTES),
                    base_score * RankingConfig.SEVERE_PENALTY_FACTOR
                ),
                (
                    (tag_weight < RankingConfig.MILD_PENALTY_THRESHOLD) &
                    (total_votes >= RankingConfig.MILD_PENALTY_MIN_VOTES),
                    base_score * RankingConfig.MILD_PENALTY_FACTOR
                ),
                else_ = base_score
            )

            order_by = final_score.desc() if query.sort_order == 'desc' else final_score.asc()

        elif query.sort_method == 'created_time':
            order_by = Thread.created_at.desc() if query.sort_order == 'desc' else Thread.created_at.asc()
        elif query.sort_method == 'active_time':
            order_by = Thread.last_active_at.desc() if query.sort_order == 'desc' else Thread.last_active_at.asc()
        elif query.sort_method == 'reaction_count':
            order_by = Thread.reaction_count.desc() if query.sort_order == 'desc' else Thread.reaction_count.asc()
        else:
            # 默认回退到活跃时间排序
            order_by = Thread.last_active_at.desc() if query.sort_order == 'desc' else Thread.last_active_at.asc()

        statement = statement.order_by(order_by).offset(offset).limit(limit)
        
        result = await self.session.execute(statement)
        return result.unique().all()

    async def get_user_preferences(self, user_id: int) -> Optional[UserSearchPreferences]:
        """获取用户的搜索偏好设置。"""
        result = await self.session.get(UserSearchPreferences, user_id)
        return result

    async def save_user_preferences(self, user_id: int, prefs_data: dict) -> UserSearchPreferences:
        """创建或更新用户的搜索偏好设置。"""
        prefs = await self.get_user_preferences(user_id)
        if prefs:
            for key, value in prefs_data.items():
                setattr(prefs, key, value)
        else:
            prefs = UserSearchPreferences(user_id=user_id, **prefs_data)
        
        self.session.add(prefs)
        await self.session.commit()
        await self.session.refresh(prefs)
        return prefs

    async def get_tags_for_author(self, author_id: int) -> Sequence[Tag]:
        """获取指定作者使用过的所有标签。"""
        statement = (
            select(Tag)
            .join(Thread.tags)
            .where(Thread.author_id == author_id)
            .distinct()
        )
        result = await self.session.execute(statement)
        return result.all()