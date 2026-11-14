"""批量投递通知失效事件；通知原始内容不经过消息总线。"""
import asyncio
import json

from sqlalchemy import text


class NotificationOutboxPublisher:
    """事务行锁保证并发发布器不重复领取，失败后保留待投递项。"""

    def __init__(self, sessions, connection, subject: str, batch_size: int = 128):
        if not subject or any(ch in subject for ch in "*> \t\r\n"):
            raise ValueError("通知投递 subject 无效")
        if not 1 <= batch_size <= 512:
            raise ValueError("通知投递批大小无效")
        self.sessions = sessions
        self.connection = connection
        self.subject = subject
        self.batch_size = batch_size

    async def publish_once(self) -> int:
        """至少一次投递；提交前崩溃可能重复，消费者必须幂等。"""
        async with self.sessions() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        text("""
                            SELECT id, recipient_id
                            FROM work_notification_outbox
                            WHERE published_at IS NULL
                            ORDER BY id
                            LIMIT :batch_size
                            FOR UPDATE SKIP LOCKED
                        """),
                        {"batch_size": self.batch_size},
                    )
                ).all()
                if not rows:
                    return 0
                # 整批持锁时间有界，避免网络慢时逐条超时累积。
                async with asyncio.timeout(5):
                    for row in rows:
                        payload = json.dumps(
                            {"event_id": str(row.id), "recipient_id": row.recipient_id},
                            separators=(",", ":"),
                        ).encode("utf-8")
                        await self.connection.publish(self.subject, payload)
                    # flush 不保证客户端收到；连接恢复后必须 HTTP 校准。
                    await self.connection.flush(timeout=3)
                await session.execute(
                    text("""
                        UPDATE work_notification_outbox SET published_at = now()
                        WHERE id = ANY(:ids) AND published_at IS NULL
                    """),
                    {"ids": [row.id for row in rows]},
                )
                return len(rows)

    async def prune_published(self) -> int:
        """只清理由成功发布且保留七天的传递元数据，不删除通知。"""
        async with self.sessions() as session:
            async with session.begin():
                result = await session.execute(text("""
                    DELETE FROM work_notification_outbox
                    WHERE id IN (
                        SELECT id FROM work_notification_outbox
                        WHERE published_at < now() - interval '7 days'
                        ORDER BY id LIMIT 1000 FOR UPDATE SKIP LOCKED
                    )
                """))
                return int(result.rowcount or 0)

