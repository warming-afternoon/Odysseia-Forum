from sqlalchemy import select

from models.discord_tag_sync_state import DiscordTagSyncState


class DiscordTagSyncStateRepository:
    """读取完整频道标签快照的持久化检查点。"""

    def __init__(self, session):
        """复用目录构建事务。"""
        self.session = session

    async def get_synced_channel_ids(self, channel_ids: list[int]) -> set[int]:
        """一次读取已完成完整同步的频道，正确识别合法空标签列表。"""
        if not channel_ids:
            return set()
        # 无来源标签不代表同步失败，需要独立的检查点确认完整空快照。
        result = await self.session.execute(
            select(DiscordTagSyncState.channel_id).where(
                DiscordTagSyncState.channel_id.in_(channel_ids)
            )
        )
        return set(result.scalars())
