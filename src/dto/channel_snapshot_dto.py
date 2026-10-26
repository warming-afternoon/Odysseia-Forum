from dataclasses import dataclass
from datetime import UTC, datetime

import discord


@dataclass(frozen=True)
class ChannelSnapshotDTO:
    """脱离 Discord 可变缓存和数据库会话的完整频道资料。"""

    channel_id: int
    """Discord 频道 ID"""

    guild_id: int
    """Discord 服务器 ID"""

    guild_name: str | None
    """服务器名称，网关尚未就绪时可能未知"""

    name: str
    """频道名称"""

    topic: str | None
    """频道简介"""

    category_id: int | None
    """分类 ID，无分类时为空"""

    category_name: str | None
    """分类名称，无分类或未知时为空"""

    channel_type: int
    """Discord 原生频道类型整数"""

    created_at: datetime
    """Discord 创建时间，使用不带时区的 UTC"""

    observed_at: datetime
    """开始采集快照的 UTC 时间，用于拒绝过期响应"""

    available_tags: tuple[tuple[int, str], ...] = ()
    """按 Discord 展示顺序保存的原生标签 ID 和名称"""

    @classmethod
    def from_channel(cls, channel, observed_at: datetime):
        """立即复制频道资料，避免等待期间网关对象发生变化。"""
        # 网关就绪前，REST 返回的频道可能没有完整的服务器和分类名称。
        category = channel.category
        return cls(
            channel_id=channel.id,
            guild_id=channel.guild.id,
            guild_name=getattr(channel.guild, "name", None) or None,
            name=channel.name,
            topic=channel.topic,
            category_id=channel.category_id,
            category_name=category.name if category else None,
            channel_type=channel.type.value,
            created_at=channel.created_at.astimezone(UTC).replace(tzinfo=None),
            observed_at=observed_at,
            available_tags=tuple((tag.id, tag.name) for tag in channel.available_tags)
            if isinstance(channel, discord.ForumChannel)
            else (),
        )
