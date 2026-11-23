"""作品公告和规则的读者确认记录。"""

from sqlalchemy import func, literal, select
from sqlalchemy.dialects.postgresql import insert

from models.work import Work
from models.work_ack import WorkAck
from models.work_announcement import WorkAnnouncement


async def advance_work_ack(session, work_id, user_id, kind, version):
    """以原子 upsert 推进确认记录，较旧的确认请求不会覆盖新版本。"""
    statement = insert(WorkAck).values(
        work_id=work_id, user_id=user_id, kind=kind, confirmed_version=version
    )
    await session.execute(
        statement.on_conflict_do_update(
            index_elements=[WorkAck.work_id, WorkAck.user_id, WorkAck.kind],
            set_={"confirmed_version": statement.excluded.confirmed_version},
            where=WorkAck.confirmed_version < statement.excluded.confirmed_version,
        )
    )


async def work_ack_states(session, work_ids, user_id):
    """批量比较可读公告和当前规则与读者的确认进度。"""
    if not user_id or not work_ids:
        return {}
    # 分别比较公告与规则，避免同一作品的两个序号互相影响。
    latest = (
        select(
            WorkAnnouncement.work_id.label("work_id"),
            func.max(WorkAnnouncement.version).label("version"),
            literal("announcement").label("kind"),
        )
        .where(
            WorkAnnouncement.work_id.in_(work_ids),
            WorkAnnouncement.withdrawn_at.is_(None),
        )
        .group_by(WorkAnnouncement.work_id)
        .union_all(
            select(Work.id, Work.rules_version, literal("rules")).where(
                Work.id.in_(work_ids), Work.rules_version > 0
            )
        )
        .subquery()
    )
    pending = await session.execute(
        select(latest.c.work_id, latest.c.kind)
        .outerjoin(
            WorkAck,
            (WorkAck.work_id == latest.c.work_id)
            & (WorkAck.user_id == user_id)
            & (WorkAck.kind == latest.c.kind),
        )
        .where(latest.c.version > func.coalesce(WorkAck.confirmed_version, 0))
    )
    states = {}
    for work_id, kind in pending:
        states.setdefault(work_id, {"announcement": False, "rules": False})[kind] = True
    return states
