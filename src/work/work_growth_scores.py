"""以 Forum 现有 UTC 日增量计算作品近期增长。"""

from datetime import timedelta

from sqlalchemy import func, select

from models.work_growth_daily import WorkGrowthDaily


def growth_scores(metric, today, days, *, positive_only=False):
    """累计指定指标的 UTC 日桶，发现增长榜可选择只展示正增长。"""
    score = func.sum(WorkGrowthDaily.delta)
    statement = (
        select(WorkGrowthDaily.work_id, score.label("score"))
        .where(
            WorkGrowthDaily.metric == metric,
            WorkGrowthDaily.day >= today - timedelta(days=days - 1),
            WorkGrowthDaily.day <= today,
        )
        .group_by(WorkGrowthDaily.work_id)
    )
    if positive_only:
        statement = statement.having(score > 0)
    return statement.subquery()
