"""保持原发现栏目的作品级筛选与净增长排序。"""

from datetime import datetime, timedelta, timezone
from fastapi import HTTPException
from sqlalchemy import func, select, or_
from api.v2.schemas.work_search_request import WorkSearchRequest
from core.preferences_repository import PreferencesRepository
from identity.canonical_user_id import CanonicalUserId
from models.work import Work
from models.work_growth_daily import WorkGrowthDaily
from work.work_preferences_service import search_preference_guild
from work.work_repository import WorkRepository
from work.work_service import WorkService


class WorkRailsService:
    """权限、频道和偏好均在排序分页之前生效。"""

    METRICS = {
        "latest": None,
        "reaction_surge": "reaction",
        "discussion_surge": "reply",
        "collection_surge": "collection",
    }

    def __init__(self, sessions):
        """沿用请求会话工厂，不缓存跨用户可见结果。"""
        self.sessions = sessions

    async def fetch(
        self, principal, rail, limit, offset, days, channels, apply_preferences
    ):
        """读取指定栏目或四栏，时间窗口包括今天的 UTC 日桶。"""
        if rail is not None and rail not in self.METRICS:
            raise HTTPException(422, "未知发现栏目")
        async with self.sessions() as session:
            query = WorkSearchRequest(
                sort_method="created_at", tag_logic="or", apply_preferences=False
            )
            # 保留发现页原有正反选偏好，显式频道覆盖偏好频道。
            if apply_preferences:
                prefs = await PreferencesRepository(session).get_user_preferences(
                    CanonicalUserId(str(principal["id"])),
                    search_preference_guild(principal),
                )
                if prefs:
                    for source, target in (
                        ("preferred_channels", "channel_ids"),
                        ("include_tags", "include_tags"),
                        ("exclude_tags", "exclude_tags"),
                        ("include_keywords", "keywords"),
                        ("exclude_keywords", "exclude_keywords"),
                        ("exclude_authors", "exclude_authors"),
                        (
                            "exclude_keyword_exemption_markers",
                            "exclude_keyword_exemption_markers",
                        ),
                    ):
                        value = getattr(prefs, source, None)
                        if value is not None:
                            setattr(query, target, value)
            if channels is not None:
                query.channel_ids = channels
            ignored = (
                principal.get("config", {})
                .get("discovery", {})
                .get("ignore_channel_ids", [])
            )
            filters = (
                [or_(
                    Work.source_platform != "discord",
                    Work.source_channel_id.not_in([str(value) for value in ignored]),
                )]
                if ignored else []
            )
            if query.channel_ids == []:
                return [] if rail else {name: [] for name in self.METRICS}
            today = datetime.now(timezone.utc).date()
            result = {}
            for name in ([rail] if rail else self.METRICS):
                metric = self.METRICS[name]
                ranking = None
                scores = None
                conditions = list(filters)
                if metric:
                    # 日增量允许负值，只有净增长为正的作品进入增长榜。
                    scores = (
                        select(
                            WorkGrowthDaily.work_id,
                            func.sum(WorkGrowthDaily.delta).label("score"),
                        )
                        .where(
                            WorkGrowthDaily.metric == metric,
                            WorkGrowthDaily.day >= today - timedelta(days=days - 1),
                            WorkGrowthDaily.day <= today,
                        )
                        .group_by(WorkGrowthDaily.work_id)
                        .having(func.sum(WorkGrowthDaily.delta) > 0)
                        .subquery()
                    )
                    ranking = scores.c.score
                rows, _ = await WorkRepository(session).page(
                    principal, offset, limit,
                    advanced=query,
                    extra_filters=conditions,
                    ranking_expression=ranking,
                    ranking_source=scores,
                    count_total=False,
                )
                result[name] = await WorkService(session).project(rows)
            return result[rail] if rail else result
