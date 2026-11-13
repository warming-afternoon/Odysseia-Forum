"""主讨论帖完整反应列表及最高单表情计数投影。"""

from sqlalchemy import select, delete, text
from sqlalchemy.dialects.postgresql import insert
from models.thread import Thread
from models.work_reaction import WorkReaction
from models.work_statistics import WorkStatistics


class WorkReactionProjectionService:
    """只聚合主帖，子卡反应不进入作品指标。"""

    def __init__(self, session):
        """复用调用方作品锁及数据库事务。"""
        self.session = session

    async def refresh(self, work_id):
        """完整列表按表情聚合，作品点赞数取列表中的最大计数。"""
        await self.session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": work_id})
        rows = (await self.session.execute(select(
            Thread.reactions, Thread.reaction_emojis, Thread.reaction_count,
        ).where(
            Thread.work_id == work_id, Thread.role == "main",
            Thread.deleted_at.is_(None),
        ).execution_options(all_platforms=True))).all()
        totals, metadata = {}, {}
        for counts, emojis, _ in rows:
            metadata.update(emojis or {})
            for key, count in (counts or {}).items():
                if type(count) is not int or count < 0:
                    raise ValueError("无效表情计数")
                totals[key] = totals.get(key, 0) + count
        await self.session.execute(delete(WorkReaction).where(WorkReaction.work_id == work_id))
        if totals:
            await self.session.execute(insert(WorkReaction), [
                {"work_id": work_id, "emoji": key, "emoji_data": metadata.get(key, {}), "count": value}
                for key, value in totals.items()
            ])
        # 尚未同步完整列表的旧帖保留已知计数，不凭空生成历史表情。
        highest = max(totals.values(), default=max((max(row[2] or 0, 0) for row in rows), default=0))
        statement = insert(WorkStatistics).values(work_id=work_id, reaction_count=highest)
        await self.session.execute(statement.on_conflict_do_update(
            index_elements=[WorkStatistics.work_id], set_={"reaction_count": highest},
        ))
        return highest
