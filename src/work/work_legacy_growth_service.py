"""将已有 Redis 日榜幂等映射到作品，不推算缺失历史。"""

from datetime import timedelta
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from models.thread import Thread
from models.work_event import WorkEvent
from models.work_growth_daily import WorkGrowthDaily
from shared.time_utils import utc_now


class WorkLegacyGrowthService:
    """切换前停止旧日榜写入，再以原始 UTC 日期导入保留期数据。"""

    def __init__(self, session, redis):
        """调用方提供事务及只读旧 Redis 客户端。"""
        self.session = session
        self.redis = redis

    async def import_window(self, cutoff_day, days=90):
        """只映射确定存在的日桶，重复执行不增加同一旧帖日计数。"""
        if not 1 <= days <= 90:
            raise ValueError("历史窗口必须在 1 至 90 天之间")
        # 维护窗口先停止旧写者，迁移和导入提交后再启动新写者。
        # 全局切换标记避免后续再次导入包含新日桶时期的 Redis 数据。
        marker = "legacy-growth:cutover"
        if await self.session.get(WorkEvent, marker) is not None:
            return 0
        await self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended('legacy-growth:cutover',0))")
        )
        if await self.session.get(WorkEvent, marker) is not None:
            return 0
        imported = 0
        for distance in range(days):
            day = cutoff_day - timedelta(days=distance)
            for metric in ("reaction", "reply", "collection"):
                key = f"trend:{metric}:{day:%Y%m%d}"
                cursor = 0
                while True:
                    cursor, entries = await self.redis.zscan(key, cursor=cursor, count=500)
                    values = {}
                    for member, score in entries:
                        identifier = member.decode() if isinstance(member, bytes) else str(member)
                        if identifier.isdecimal() and float(score).is_integer():
                            values[int(identifier)] = int(score)
                    if values:
                        rows = (await self.session.execute(select(Thread).where(
                            Thread.platform == "discord", Thread.thread_id.in_(values),
                            Thread.work_id.is_not(None),
                        ).execution_options(all_platforms=True))).scalars().all()
                        for thread in rows:
                            if metric == "reaction" and thread.role != "main":
                                continue
                            event_id = f"legacy-growth:discord:{thread.thread_id}:{metric}:{day}"
                            accepted = (await self.session.execute(insert(WorkEvent).values(
                                event_id=event_id,
                                payload={"kind": "legacy_growth", "metric": metric, "day": str(day),
                                    "delta": values[thread.thread_id], "semantics": "legacy_positive_only"},
                                received_at=utc_now(),
                            ).on_conflict_do_nothing().returning(WorkEvent.event_id))).scalar_one_or_none()
                            if accepted is None:
                                continue
                            statement = insert(WorkGrowthDaily).values(
                                work_id=thread.work_id, day=day, metric=metric, delta=values[thread.thread_id]
                            )
                            await self.session.execute(statement.on_conflict_do_update(
                                index_elements=["work_id", "day", "metric"],
                                set_={"delta": WorkGrowthDaily.delta + statement.excluded.delta},
                            ))
                            imported += 1
                    if int(cursor) == 0:
                        break
        self.session.add(WorkEvent(
            event_id=marker,
            payload={"cutoff_day": str(cutoff_day), "days": days, "imported": imported},
            received_at=utc_now(),
        ))
        return imported
