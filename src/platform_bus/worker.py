"""运行平台 JetStream 消费、事务发件箱交付及绑定修复。"""

import asyncio
import json
import logging
import os
from urllib.parse import quote

import httpx
import nats
from nats.errors import TimeoutError as NatsTimeout
from sqlalchemy import text
from sqlalchemy.exc import OperationalError, TimeoutError as PoolTimeout

from shared.database import AsyncSessionFactory
from models.work import Work
from platform_bus.auto_follow import apply_auto_follow
from platform_bus.bindings import apply_bindings
from platform_bus.projections import apply_projection, MESSAGE_TIMES, WORK_STATES

log = logging.getLogger(__name__)
BACKOFF = (30, 60, 300, 900, 1800, 3600, 7200)
BINDING = "account.discord_binding_changed"


def accepts(module, event):
    """核对发布方和消费者的协议范围。"""
    kind = event["type"]
    if kind == BINDING:
        return module == "forum" and event["source"] == "core"
    if kind in WORK_STATES:
        return module in {"search", "collections"} and event["source"] == "works"
    if kind in MESSAGE_TIMES:
        return module == "works" and event["source"] == "chat"
    if kind in {"user.content_hidden", "user.content_unhidden"}:
        return module in {"works", "search"} and event["source"] == "core"
    return False


async def current_bindings(client, user_id):
    """从权威内网接口恢复完整绑定状态；不能用公开资料推断绑定。"""
    response = await client.get(
        "/internal/users/" + quote(user_id, safe="") + "/discord-bindings",
        headers={"x-ody-internal-token": os.environ["FORUM_INTERNAL_TOKEN"]},
    )
    response.raise_for_status()
    snapshot = response.json()
    if snapshot["userId"] != user_id:
        raise ValueError("绑定快照主体不匹配")
    return snapshot


async def handle(module, subject, raw, client, sessions=AsyncSessionFactory):
    """去重记录和数据库修改在同一事务内完成。"""
    event = json.loads(raw)
    if subject != "ody.events." + event["type"] or not event["eventId"]:
        raise ValueError("事件信封无效")
    if not accepts(module, event):
        return
    async with sessions.begin() as session:
        inserted = await session.execute(
            text("""
            INSERT INTO platform_inbox(consumer,event_id) VALUES(:module,:id)
            ON CONFLICT DO NOTHING RETURNING event_id"""),
            {"module": module, "id": event["eventId"]},
        )
        if inserted.scalar_one_or_none() is None:
            return
        if event["type"] == BINDING:
            expected = event["data"]
            snapshot = await current_bindings(client, expected["userId"])
            if int(snapshot["version"]) < int(expected["version"]):
                raise RuntimeError("权威快照版本暂时落后")
            await apply_bindings(session, snapshot)
        else:
            await apply_projection(session, module, event)


async def consume(module, js, client):
    """绑定预配置拉取消费者，按已投递次数延迟重试。"""
    sub = await js.pull_subscribe_bind(
        durable=module,
        stream="ODY_EVENTS",
        inbox_prefix=("_INBOX_" + module + ".").encode(),
    )
    while True:
        try:
            messages = await sub.fetch(1, timeout=2)
        except NatsTimeout:
            continue
        for message in messages:
            try:
                async with asyncio.TaskGroup() as group:
                    heartbeat = group.create_task(keep_alive(message))
                    try:
                        await handle(module, message.subject, message.data, client)
                    finally:
                        heartbeat.cancel()
            except ExceptionGroup:
                # 依赖失败或协议不匹配均保留重投机会；超过上限由总线记录死信通知。
                await message.nak(
                    delay=BACKOFF[
                        min(message.metadata.num_delivered - 1, len(BACKOFF) - 1)
                    ]
                )
                log.warning(
                    "平台事件处理失败 module=%s seq=%s",
                    module,
                    message.metadata.sequence.stream,
                )
            else:
                await message.ack()


async def keep_alive(message):
    """长事务处理期间续期消费者确认窗口。"""
    while True:
        await asyncio.sleep(10)
        await message.in_progress()


async def deliver(js, client, sessions=AsyncSessionFactory):
    """逐项发送，网络失败的任务延期，新的策略通过权威回查收敛。"""
    from platform_gateway.policy import chat_policy

    async with sessions.begin() as session:
        row = (
            (
                await session.execute(
                    text("""
            SELECT * FROM platform_outbox WHERE sent_at IS NULL AND available_at<=now()
            ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED""")
                )
            )
            .mappings()
            .first()
        )
        if not row:
            return False
        try:
            if row["kind"] == "auto_follow":
                # 本地任务的写入失败后回滚子事务，父事务仍可更新下次重试时间。
                async with session.begin_nested():
                    await apply_auto_follow(
                        session, client, row["work_id"], row["payload"]["userId"]
                    )
            elif row["kind"] == "chat_sync":
                await session.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                    {"key": "platform-chat:" + str(row["work_id"])},
                )
                work = await session.get(Work, row["work_id"])
                if work is not None:
                    policy = await chat_policy(session, work)
                    result = await client.post(
                        "/internal/chat/sync-work",
                        json=policy,
                        headers={
                            "x-ody-internal-token": os.environ[
                                "FORUM_CORE_CHAT_SYNC_TOKEN"
                            ]
                        },
                    )
                    if result.status_code != 204:
                        raise RuntimeError("聊天策略尚未确认")
            else:
                await js.publish(
                    "ody.events." + row["kind"],
                    json.dumps(row["payload"]).encode(),
                    stream="ODY_EVENTS",
                    headers={"Nats-Msg-Id": row["event_id"]},
                )
        except Exception as error:
            await session.execute(
                text("""UPDATE platform_outbox SET attempts=attempts+1,
                available_at=now()+make_interval(secs => CAST(:seconds AS int)) WHERE id=:id"""),
                {
                    "id": row["id"],
                    "seconds": BACKOFF[min(row["attempts"], len(BACKOFF) - 1)],
                },
            )
            log.warning(
                "平台任务等待重试 id=%s error=%s", row["id"], type(error).__name__
            )
        else:
            await session.execute(
                text("UPDATE platform_outbox SET sent_at=now() WHERE id=:id"),
                {"id": row["id"]},
            )
        return True


async def publish_loop(js, client):
    """持续处理事务发件箱。"""
    while True:
        try:
            delivered = await deliver(js, client)
        except (OperationalError, PoolTimeout):
            log.warning("数据库暂不可用，平台发件箱等待重连")
            await asyncio.sleep(5)
            continue
        if not delivered:
            await asyncio.sleep(0.5)


async def run():
    """启动独立进程；每个模块只使用自己的 NATS 凭证。"""
    connections = []
    try:
        async with httpx.AsyncClient(
            base_url=os.environ["FORUM_CORE_URL"], timeout=8
        ) as client:
            async with asyncio.TaskGroup() as group:
                for module in ("works", "search", "collections", "forum"):
                    conn = await nats.connect(
                        os.environ["FORUM_NATS_URL"],
                        user=module,
                        password=os.environ[
                            "FORUM_NATS_" + module.upper() + "_PASSWORD"
                        ],
                        name="forum-" + module,
                        inbox_prefix="_INBOX_" + module,
                        max_reconnect_attempts=-1,
                    )
                    connections.append(conn)
                    js = conn.jetstream()
                    group.create_task(consume(module, js, client))
                    if module == "works":
                        group.create_task(publish_loop(js, client))
    finally:
        for conn in connections:
            await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
