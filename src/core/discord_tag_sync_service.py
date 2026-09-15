from sqlalchemy import func, select, text, update

from core.tag_repository import TagRepository
from models import Tag
from models.tag_binding import TagBinding
from models.operation_log import OperationLog
from models.tag_notification_task import TagNotificationTask


class DiscordTagSyncService:
    """处理完整频道快照并在同一事务内保存转换与通知任务。"""

    def __init__(self, session):
        """复用调用方事务中的数据库会话。"""
        self.session = session

    async def apply(self, event):
        """按 DC ID 区分新增、改名与删除，保留已转换实体身份。"""
        await self.session.execute(text("SELECT pg_advisory_xact_lock(73902141)"))
        latest = (
            await self.session.execute(
                select(func.max(Tag.discord_synced_at)).where(
                    Tag.discord_channel_id == event.channel_id
                )
            )
        ).scalar_one()
        if latest and latest > event.observed_at:
            return
        previous = list(
            (
                await self.session.execute(
                    select(Tag).where(
                        Tag.discord_channel_id == event.channel_id,
                        Tag.source == "discord",
                    )
                )
            ).scalars()
        )
        current = await TagRepository(self.session).get_or_create_tags(event.tags)
        for tag in current:
            if tag.source == "discord":
                tag.discord_channel_id = event.channel_id
                tag.discord_synced_at = event.observed_at
        for tag in previous:
            if tag.discord_tag_id in event.tags:
                continue
            tag.source = "custom"
            tag.category = None
            tag.discord_synced_at = event.observed_at
            # 转换绑定而不结束轮次，标签数不变；原生投票入口已停用。
            await self.session.execute(
                update(TagBinding)
                .where(
                    TagBinding.tag_id == tag.id,
                    TagBinding.binding_source == "discord_sync",
                    TagBinding.ended_at.is_(None),
                )
                .values(binding_source="local", upvotes=0, downvotes=0)
            )
            self.session.add(TagNotificationTask(kind="converted", tag_id=tag.id))
            self.session.add(
                OperationLog(
                    type="tag.pool.convert",
                    target_type="tag",
                    target_id=tag.id,
                    tag_id=tag.id,
                    detail={
                        "tag_name": tag.name,
                        "discord_channel_id": str(event.channel_id),
                        "target_id_kind": "internal",
                    },
                )
            )
        await self.session.flush()
