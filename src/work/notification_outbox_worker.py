"""V2 通知待投递事件后台工作器。"""
import asyncio

from work.notification_outbox_publisher import NotificationOutboxPublisher


class NotificationOutboxWorker:
    """复用 Forum 认证连接，后台处理持久待投递事件。"""

    def __init__(self, sessions, auth, subject: str, batch_interval: float = 1.0):
        if not 0.1 <= batch_interval <= 10:
            raise ValueError("通知批次间隔无效")
        self.batch_interval = batch_interval
        self.sessions = sessions
        self.auth = auth
        self.subject = subject
        self.task = None

    async def start(self):
        if self.task is None:
            self.task = asyncio.create_task(self.run())

    async def close(self):
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None

    async def run(self):
        import logging
        logger = logging.getLogger(__name__)
        next_prune = 0.0
        while True:
            try:
                await self.auth.start()
                if not self.auth.ready or self.auth.connection is None:
                    raise RuntimeError("认证总线不可用")
                publisher = NotificationOutboxPublisher(
                    self.sessions, self.auth.connection, self.subject
                )
                count = await publisher.publish_once()
                now = asyncio.get_running_loop().time()
                if now >= next_prune:
                    await publisher.prune_published()
                    next_prune = now + 300
                # 限制每个发布器的扇出速率，避免一次公告让所有在线用户同时查库。
                # 多个发布器的总速率会相加，部署前须按实例数一起压测和配置。
                await asyncio.sleep(self.batch_interval if count else 1)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("通知 outbox 投递失败，稍后重试")
                await asyncio.sleep(3)
