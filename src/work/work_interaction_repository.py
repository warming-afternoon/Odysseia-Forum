"""写入作品互动关系及其统计投影。"""

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from models.thread_follow import ThreadFollow
from models.work_growth_daily import WorkGrowthDaily
from models.work_like import WorkLike
from models.work_statistics import WorkStatistics
from shared.time_utils import utc_now


async def change_follow(session, work_id, user_id, *, follow, automatic=False):
    """手动取关永久留下标记，后台关注仅能启用未主动取消的关系。"""
    now = utc_now()
    row = insert(ThreadFollow).values(
        work_id=work_id,
        fluxer_user_id=user_id,
        followed_at=now,
        active_flag=follow,
        manual_unfollowed_at=None if follow else now,
    )
    values = {"active_flag": follow}
    condition = None
    if follow:
        values.update(followed_at=now, last_viewed_at=None)
        condition = ThreadFollow.active_flag.is_(False)
        if automatic:
            condition = condition & ThreadFollow.manual_unfollowed_at.is_(None)
    else:
        values["manual_unfollowed_at"] = now
    await session.execute(
        row.on_conflict_do_update(
            constraint="uq_thread_follow_user_work", set_=values, where=condition
        )
    )


async def change_like(session, work_id, user_id, *, like):
    """仅在点赞明细改变时同步更新统计和 UTC 日增量。"""
    if like:
        mutation = (
            insert(WorkLike)
            .values(work_id=work_id, user_id=user_id)
            .on_conflict_do_nothing(index_elements=[WorkLike.work_id, WorkLike.user_id])
        )
    else:
        mutation = delete(WorkLike).where(
            WorkLike.work_id == work_id, WorkLike.user_id == user_id
        )
    if await session.scalar(mutation.returning(WorkLike.work_id)) is None:
        return False
    delta = 1 if like else -1
    # 缺失汇总时从明细恢复基数，现有汇总按实际变更增减。
    count = select(func.count()).where(WorkLike.work_id == work_id).scalar_subquery()
    statistics = insert(WorkStatistics).values(
        work_id=work_id, platform_like_count=count
    )
    await session.execute(
        statistics.on_conflict_do_update(
            index_elements=[WorkStatistics.work_id],
            set_={"platform_like_count": WorkStatistics.platform_like_count + delta},
        )
    )
    growth = insert(WorkGrowthDaily).values(
        work_id=work_id, day=utc_now().date(), metric="reaction", delta=delta
    )
    await session.execute(
        growth.on_conflict_do_update(
            index_elements=[
                WorkGrowthDaily.work_id,
                WorkGrowthDaily.day,
                WorkGrowthDaily.metric,
            ],
            set_={"delta": WorkGrowthDaily.delta + delta},
        )
    )
    return True
