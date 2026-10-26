from discord import ChannelType

from core.channel_repository import ChannelRepository
from core.discord_tag_source_repository import DiscordTagSourceRepository
from core.discord_tag_sync_state_repository import DiscordTagSyncStateRepository
from core.thread_repository import ThreadRepository
from dto.meta.category_meta import CategoryMeta
from dto.meta.channel_meta import ChannelMeta
from dto.meta.guild_meta import GuildMeta


class ApiChannelMetadataService:
    """从同一数据库事务批量构建 API 论坛目录，不访问 Discord 或 Redis。"""

    def __init__(self, session):
        """复用调用方一致性读取会话。"""
        self.session = session

    async def load_channels(self) -> tuple[set[int], dict[int, ChannelMeta]]:
        """读取完整搜索索引范围，单独构建只包含论坛的展示目录。"""
        indexed_ids = await ThreadRepository(self.session).get_all_indexed_channel_ids()
        records = await ChannelRepository(self.session).get_by_channel_ids(
            list(indexed_ids)
        )
        missing = set(indexed_ids) - {item.channel_id for item in records}
        if missing:
            raise RuntimeError(f"已索引频道尚未入库: {sorted(missing)}")

        channels: dict[int, ChannelMeta] = {}
        for item in records:
            if item.channel_type is None:
                raise RuntimeError(f"已索引频道类型尚未同步: {item.channel_id}")
            if item.channel_type != ChannelType.forum.value:
                continue
            # 不把缺失名称降级成空目录，避免服务健康但页面内容不完整。
            if (
                not item.name.strip()
                or not (item.guild_name or "").strip()
                or item.synced_at is None
                or (
                    item.category_id is not None
                    and not (item.category_name or "").strip()
                )
            ):
                raise RuntimeError(f"论坛频道资料尚未同步完整: {item.channel_id}")
            channels[item.channel_id] = ChannelMeta(
                id=item.channel_id,
                name=item.name,
                guild=GuildMeta(id=item.guild_id, name=item.guild_name),
                category_id=item.category_id,
                category=CategoryMeta(id=item.category_id, name=item.category_name)
                if item.category_id is not None
                else None,
            )

        # 检查点允许区分真实空标签与尚未执行过完整标签同步的频道。
        synced = await DiscordTagSyncStateRepository(
            self.session
        ).get_synced_channel_ids(list(channels))
        if unsynced := set(channels) - synced:
            raise RuntimeError(f"论坛原生标签尚未完成同步: {sorted(unsynced)}")
        tags = await DiscordTagSourceRepository(self.session).get_active_channel_tags(
            list(channels)
        )
        for channel_id, channel in channels.items():
            channel.available_tags = tags.get(channel_id, [])
        # 搜索范围保留原始索引集合，不能用目录子集触发全范围查询优化。
        return set(indexed_ids), channels
