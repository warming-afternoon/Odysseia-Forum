from core.channel_repository import ChannelRepository
from core.discord_tag_sync_service import DiscordTagSyncService
from core.tag_pool_cache_service import TagPoolCacheService
from dto.channel_snapshot_dto import ChannelSnapshotDTO
from dto.events.discord_tags_snapshot import DiscordTagsSnapshot


class ChannelMetadataService:
    """在同一事务中持久化频道资料和可信的完整标签快照。"""

    def __init__(self, session_factory, redis_client):
        """复用会话工厂和 Redis，不持有长期数据库会话。"""
        self.session_factory = session_factory
        self.pool_cache = TagPoolCacheService(redis_client)

    async def save(
        self, snapshots: list[ChannelSnapshotDTO], *, sync_tags: bool = False
    ) -> set[int]:
        """提交新资料，事务失败时同时回滚频道和标签变更。"""
        pool_changed = False
        async with self.session_factory() as session, session.begin():
            # 先获取标签事务锁，与其他完整快照写入保持一致的锁顺序。
            if sync_tags:
                for item in sorted(snapshots, key=lambda item: item.channel_id):
                    pool_changed |= await DiscordTagSyncService(session).apply(
                        DiscordTagsSnapshot(
                            item.channel_id, dict(item.available_tags), item.observed_at
                        )
                    )
            accepted = await ChannelRepository(session).upsert_snapshots(snapshots)
        # 只在提交成功后失效标签池，失败时不会暴露未提交资料。
        if pool_changed:
            await self.pool_cache.invalidate()
        return accepted
