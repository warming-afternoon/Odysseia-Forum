"""频道同步服务 —— 从 Discord API 按需索引频道"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from core.channel_repository import ChannelRepository
from dto.channel_snapshot_dto import ChannelSnapshotDTO
from models.channel import Channel
from shared.time_utils import utc_now

logger = logging.getLogger(__name__)

DISCORD_API_BASE = "https://discord.com/api/v10"


class ChannelSyncService:
    """从 Discord REST API 获取频道信息并写入 channel 表"""

    def __init__(self, bot_token: str):
        self.bot_token = bot_token

    async def fetch_and_index(
        self, session: AsyncSession, channel_id: int
    ) -> Optional[Channel]:
        """从 Discord API 获取频道信息并写入 channel 表

        若频道已存在则跳过，保证幂等。
        """
        # 先查本地是否已有
        repository = ChannelRepository(session)
        existing = await repository.get_by_channel_id(channel_id)
        if existing:
            return existing

        # 调 Discord REST API 获取频道信息
        observed_at = utc_now()
        channel_data = await self._fetch_channel(channel_id)
        if channel_data is None:
            return None

        # REST 不含服务器和分类名称，保留空值供 BOT 完整同步补齐。
        snapshot = ChannelSnapshotDTO(
            channel_id=channel_id,
            guild_id=int(channel_data["guild_id"]),
            guild_name=None,
            name=channel_data["name"],
            topic=channel_data.get("topic"),
            category_id=int(channel_data["parent_id"])
            if channel_data.get("parent_id")
            else None,
            category_name=None,
            channel_type=int(channel_data["type"]),
            created_at=parse_discord_snowflake(channel_id),
            observed_at=observed_at,
        )
        await repository.upsert_snapshots([snapshot])
        await session.commit()
        return await repository.get_by_channel_id(channel_id)

    async def _fetch_channel(self, channel_id: int) -> Optional[dict]:
        """调 Discord REST API 获取频道信息"""
        url = f"{DISCORD_API_BASE}/channels/{channel_id}"
        headers = {
            "Authorization": f"Bot {self.bot_token}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(url, headers=headers)
                if response.status_code == 200:
                    return response.json()
                elif response.status_code == 404:
                    logger.warning(f"频道 {channel_id} 不存在或 Bot 无权访问")
                    return None
                else:
                    logger.error(
                        f"获取频道 {channel_id} 失败，HTTP {response.status_code}"
                    )
                    return None
        except Exception:
            logger.error(f"获取频道 {channel_id} 时网络异常", exc_info=True)
            return None


def parse_discord_snowflake(snowflake_id: int) -> datetime:
    """从 Discord snowflake ID 解析创建时间"""
    discord_epoch = 1420070400000
    timestamp_ms = (snowflake_id >> 22) + discord_epoch
    return datetime.fromtimestamp(timestamp_ms / 1000.0, tz=timezone.utc).replace(
        tzinfo=None
    )
