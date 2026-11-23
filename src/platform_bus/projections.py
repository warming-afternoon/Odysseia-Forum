"""按事件所属模块更新作品索引状态和聊天统计。"""

from datetime import datetime
from sqlalchemy import text
from platform_bus.auto_follow import enqueue_auto_follow

WORK_STATES = {
    "work.updated": "visible",
    "work.deleted": "deleted",
    "work.restored": "visible",
    "work.purged": "purged",
}
WORK_TIMES = {
    "work.updated": "updatedAt",
    "work.deleted": "deletedAt",
    "work.restored": "restoredAt",
    "work.purged": "purgedAt",
}
MESSAGE_TIMES = {
    "chat.message_created": ("createdAt", 0),
    "chat.message_deleted": ("deletedAt", 2),
    "chat.message_restored": ("restoredAt", 1),
}


def timestamp(value):
    """要求平台协议提供带时区的实际变更时间。"""
    parsed = datetime.fromisoformat(value)
    if parsed.utcoffset() is None:
        raise ValueError("事件时间缺少时区")
    return parsed


async def apply_projection(session, consumer, event):
    """以权威业务时间处理乱序；永久删除不能被较晚的普通更新复活。"""
    kind, data = event["type"], event["data"]
    if consumer in {"search", "collections"} and kind in WORK_STATES:
        # 本站作品 ID 是正的 64 位整数，其他来源的历史不透明 ID 不关联本地行。
        wid = str(data["workId"])
        if not wid.isascii() or not wid.isdecimal() or not 0 < int(wid) < 2**63:
            return
        args = {
            "consumer": consumer,
            "id": int(wid),
            "state": WORK_STATES[kind],
            "at": timestamp(data[WORK_TIMES[kind]]),
        }
        # 普通元数据更新不能越过删除或恢复事件的时间水位。
        if kind == "work.updated":
            return
        await session.execute(
            text("""
            INSERT INTO platform_work_projection AS p(consumer,work_id,state,changed_at)
            VALUES(:consumer,:id,:state,:at)
            ON CONFLICT(consumer,work_id) DO UPDATE SET state=excluded.state,changed_at=excluded.changed_at
            WHERE p.state<>'purged' AND (excluded.state='purged' OR
                (excluded.changed_at,excluded.state='deleted')>(p.changed_at,p.state='deleted'))"""),
            args,
        )
        if kind == "work.purged" and consumer == "collections":
            # 来源书单仍保留；只移除此作品的关联，并校正条数。
            await session.execute(
                text("""
                WITH removed AS (DELETE FROM booklist_item WHERE work_id=:id RETURNING booklist_id),
                counts AS (SELECT booklist_id,count(*) n FROM removed GROUP BY booklist_id)
                UPDATE booklist b SET item_count=greatest(0,b.item_count-counts.n)
                FROM counts WHERE b.id=counts.booklist_id"""),
                args,
            )
    elif consumer in {"works", "search"} and kind in {
        "user.content_hidden",
        "user.content_unhidden",
    }:
        hidden = kind == "user.content_hidden"
        changed = await session.execute(
            text("""
            INSERT INTO platform_hidden_author AS p(user_id,hidden,changed_at)
            VALUES(:id,:hidden,:at) ON CONFLICT(user_id) DO UPDATE
            SET hidden=excluded.hidden,changed_at=excluded.changed_at
            WHERE (excluded.changed_at,excluded.hidden)>(p.changed_at,p.hidden)
            RETURNING user_id"""),
            {
                "id": data["userId"],
                "hidden": hidden,
                "at": timestamp(data["hiddenAt" if hidden else "unhiddenAt"]),
            },
        )
        if changed.scalar_one_or_none() is not None:
            # 两个模块共享本地投影，只有实际改变隐藏状态的事务调度策略同步。
            await session.execute(
                text("""
                INSERT INTO platform_outbox(work_id,event_id,kind,payload)
                SELECT w.id,gen_random_uuid()::text,'chat_sync',jsonb_build_object('workId',w.id::text)
                FROM work w
                WHERE w.author_id=:id AND w.published_at IS NOT NULL AND w.source_platform='platform'
                """),
                {"id": data["userId"]},
            )
    elif consumer == "works" and kind in MESSAGE_TIMES:
        # 对同一消息串行计算可见性差额；先收到删除也要留下状态。
        key, rank = MESSAGE_TIMES[kind]
        raw_id = data["workId"]
        if (
            not isinstance(raw_id, str)
            or not raw_id.isascii()
            or not raw_id.isdecimal()
            or len(raw_id) > 19
        ):
            return
        wid = int(raw_id)
        if not 0 < wid < 2**63:
            return
        # 永久删除后的迟到消息、其他作品服务留下的消息不能重建讨论统计。
        work_id = await session.scalar(
            text("""
            SELECT id FROM work WHERE id=:wid AND source_platform='platform' FOR KEY SHARE"""),
            {"wid": wid},
        )
        if work_id is None:
            return
        if kind == "chat.message_created":
            # 每个收件箱去重后的新消息都可以触发关注，恢复消息不触发。
            await enqueue_auto_follow(session, work_id, data.get("authorId"))
        mid = data["messageId"]
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
            {"key": "platform-message:" + mid},
        )
        old = (
            (
                await session.execute(
                    text("""
            SELECT work_id,visible,changed_at,rank FROM platform_message_state WHERE message_id=:mid"""),
                    {"mid": mid},
                )
            )
            .mappings()
            .first()
        )
        at = timestamp(data[key])
        if old and (
            old["work_id"] != wid or (at, rank) <= (old["changed_at"], old["rank"])
        ):
            return
        visible = kind != "chat.message_deleted"
        await session.execute(
            text("""
            INSERT INTO platform_message_state(message_id,work_id,visible,changed_at,rank)
            VALUES(:mid,:wid,:visible,:at,:rank) ON CONFLICT(message_id) DO UPDATE
            SET visible=excluded.visible,changed_at=excluded.changed_at,rank=excluded.rank"""),
            {"mid": mid, "wid": wid, "visible": visible, "at": at, "rank": rank},
        )
        delta = int(visible) - int(bool(old and old["visible"]))
        if delta:
            await session.execute(
                text("""
                INSERT INTO work_statistics(work_id,display_count,view_count,collection_count,reaction_count,reply_count)
                VALUES(:wid,0,0,0,0,greatest(0,:delta)) ON CONFLICT(work_id) DO UPDATE
                SET reply_count=greatest(0,work_statistics.reply_count+:delta)"""),
                {"wid": wid, "delta": delta},
            )
