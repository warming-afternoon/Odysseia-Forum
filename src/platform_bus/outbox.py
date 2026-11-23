"""在作品写事务内记录跨模块事件及聊天策略同步。"""

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text


async def enqueue_work(session, work, kind, fields=None, *, deletion_source=None):
    """变化与交付任务同时提交；Discord 来源保留原讨论入口。"""
    times = {
        "work.deleted": "deletedAt",
        "work.restored": "restoredAt",
        "work.purged": "purgedAt",
        "work.updated": "updatedAt",
    }
    stamp = datetime.now(timezone.utc).isoformat()
    await session.flush()
    data = {"workId": str(work.id), times[kind]: stamp}
    if kind == "work.updated":
        data["fields"] = list(fields or ["status"])
    if kind == "work.deleted" and deletion_source:
        data["source"] = deletion_source
    event_id = str(uuid4())
    event = {
        "eventId": event_id,
        "source": "works",
        "type": kind,
        "occurredAt": stamp,
        "data": data,
    }
    jobs = [(event_id, kind, event)]
    if kind == "work.restored":
        # 恢复后提醒搜索和书单重新读取正本的字段，聊天只需同步一次。
        update_id = str(uuid4())
        jobs.append(
            (
                update_id,
                "work.updated",
                {
                    **event,
                    "eventId": update_id,
                    "type": "work.updated",
                    "data": {
                        "workId": str(work.id),
                        "fields": ["status"],
                        "updatedAt": stamp,
                    },
                },
            )
        )
    if work.source_platform == "platform" and kind != "work.purged":
        jobs.append((str(uuid4()), "chat_sync", {"workId": str(work.id)}))
    for identifier, action, payload in jobs:
        await session.execute(
            text("""
            INSERT INTO platform_outbox(work_id,event_id,kind,payload)
            VALUES(:wid,:id,:kind,CAST(:body AS jsonb))"""),
            {
                "wid": work.id,
                "id": identifier,
                "kind": action,
                "body": json.dumps(payload),
            },
        )
