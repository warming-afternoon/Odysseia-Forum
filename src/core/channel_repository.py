from sqlalchemy import case, func, or_, select
from sqlalchemy.dialects.postgresql import insert

from dto.channel_snapshot_dto import ChannelSnapshotDTO
from models.channel import Channel


class ChannelRepository:
    """按频道 ID 批量写入资料，不提交调用方事务。"""

    def __init__(self, session):
        """复用调用方数据库会话。"""
        self.session = session

    async def upsert_snapshots(self, snapshots: list[ChannelSnapshotDTO]) -> set[int]:
        """批量保存较新快照并返回实际接受的频道 ID。"""
        if not snapshots:
            return set()
        # 固定锁定顺序，避免并发批量更新以相反顺序获取频道行锁。
        values = [
            {
                "channel_id": item.channel_id,
                "guild_id": item.guild_id,
                "guild_name": item.guild_name,
                "name": item.name,
                "topic": item.topic,
                "category_id": item.category_id,
                "category_name": item.category_name,
                "channel_type": item.channel_type,
                "created_at": item.created_at,
                "synced_at": item.observed_at,
            }
            for item in sorted(snapshots, key=lambda item: item.channel_id)
        ]
        stmt = insert(Channel).values(values)
        incoming = stmt.excluded
        stmt = stmt.on_conflict_do_update(
            index_elements=["channel_id"],
            set_={
                "guild_id": incoming.guild_id,
                "guild_name": func.coalesce(incoming.guild_name, Channel.guild_name),
                "name": incoming.name,
                "topic": incoming.topic,
                "category_id": incoming.category_id,
                "category_name": case(
                    (
                        Channel.category_id == incoming.category_id,
                        func.coalesce(incoming.category_name, Channel.category_name),
                    ),
                    else_=incoming.category_name,
                ),
                "channel_type": incoming.channel_type,
                "synced_at": incoming.synced_at,
            },
            where=or_(
                Channel.synced_at.is_(None), Channel.synced_at <= incoming.synced_at
            ),
        ).returning(Channel.channel_id)
        return set((await self.session.execute(stmt)).scalars())

    async def get_by_channel_id(self, channel_id: int) -> Channel | None:
        """读取频道并刷新当前会话中可能已存在的旧实例。"""
        # upsert 不经过 ORM 状态同步，重新查询时显式刷新已加载属性。
        result = await self.session.execute(
            select(Channel)
            .where(Channel.channel_id == channel_id)
            .execution_options(populate_existing=True)
        )
        return result.scalar_one_or_none()
