import logging
from collections import defaultdict

import discord
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlmodel import select

from core.thread_repository import ThreadRepository
from dto.search import UCB1ConfigDTO
from models import BotConfig
from shared.discord_utils import DiscordUtils
from shared.enum.search_config_type import SearchConfigDefaults, SearchConfigType

logger = logging.getLogger(__name__)


class CacheService:
    """
    缓存 BOT 共享数据的服务<br>
    目前存储的数据包括：已索引的频道、全局搜索配置 BotConfig
    """

    def __init__(self, bot, session_factory: async_sessionmaker):
        self.bot = bot
        self.session_factory = session_factory
        self.indexed_channel_ids: set[int] = set()
        self.indexed_channels: dict[int, discord.ForumChannel] = {}
        # guild_id -> {channel_id -> ForumChannel}
        self.guild_channels: dict[int, dict[int, discord.ForumChannel]] = {}
        self.bot_configs: dict[SearchConfigType, BotConfig] = {}
        logger.debug("CacheService 已初始化")

    async def build_or_refresh_cache(self):
        """
        从数据库加载数据，构建或刷新所有缓存。
        这个方法应该在机器人启动时和索引更新后被调用。
        """
        logger.debug("正在刷新 CacheService...")
        await self._refresh_indexed_channels_cache()
        await self.refresh_bot_config_cache()
        logger.info(
            f"CacheService 刷新完毕。缓存了 {len(self.indexed_channel_ids)} 个频道ID，"
            f"{len(self.indexed_channels)} 个频道对象，涉及 {len(self.guild_channels)} 个服务器；"
            f"同步缓存了 {len(self.bot_configs)} 个 BotConfig 配置项。"
        )

    async def _refresh_indexed_channels_cache(self):
        """刷新已索引频道缓存。"""
        async with self.session_factory() as session:
            thread_service = ThreadRepository(session)
            indexed_channel_ids = await thread_service.get_all_indexed_channel_ids()
            self.indexed_channel_ids = set(indexed_channel_ids)

        new_channel_cache: dict[int, discord.ForumChannel] = {}
        new_guild_channels: dict[int, dict[int, discord.ForumChannel]] = defaultdict(dict)

        for channel_id in self.indexed_channel_ids:
            # 优先从缓存获取频道，失败则从 API 获取
            channel = await DiscordUtils.get_channel_safely(self.bot, channel_id)
            if channel and isinstance(channel, discord.ForumChannel):
                new_channel_cache[channel_id] = channel
                new_guild_channels[channel.guild.id][channel_id] = channel
            elif channel:
                logger.warning(f"频道 (ID: {channel_id}) 不是论坛频道，已跳过。")

        self.indexed_channels = new_channel_cache
        self.guild_channels = dict(new_guild_channels)

    async def refresh_bot_config_cache(self):
        """刷新 BotConfig 缓存。"""
        async with self.session_factory() as session:
            result = await session.execute(select(BotConfig))
            all_configs = result.scalars().all()

        self.bot_configs = {
            SearchConfigType(config.type): config
            for config in all_configs
            if config.type in SearchConfigType._value2member_map_
        }

    async def get_bot_config(self, config_type: SearchConfigType) -> BotConfig | None:
        """从缓存获取配置；如未命中则自动刷新后重试。"""
        config = self.bot_configs.get(config_type)
        if config is not None:
            return config

        logger.info(f"配置缓存未命中: {config_type.name}. 正在尝试刷新 CacheService 配置缓存...")
        await self.refresh_bot_config_cache()
        config = self.bot_configs.get(config_type)
        if config is None:
            logger.error(
                f"刷新缓存后仍然找不到配置: {config_type.name}. "
                f"请检查数据库中是否存在此配置项，或运行一次初始化。"
            )
        return config

    async def get_ucb1_config(self) -> UCB1ConfigDTO:
        """
        获取 UCB1 算法所需的三个配置参数，封装为 DTO 返回。

        从缓存中获取总展示次数、探索因子和强度权重
        """
        total_disp_conf = await self.get_bot_config(
            SearchConfigType.TOTAL_DISPLAY_COUNT
        )
        ucb_factor_conf = await self.get_bot_config(
            SearchConfigType.UCB1_EXPLORATION_FACTOR
        )
        strength_conf = await self.get_bot_config(
            SearchConfigType.STRENGTH_WEIGHT
        )

        total_display_count = (
            total_disp_conf.value_int
            if total_disp_conf and total_disp_conf.value_int is not None
            else 1
        )
        exploration_factor = (
            ucb_factor_conf.value_float
            if ucb_factor_conf and ucb_factor_conf.value_float is not None
            else SearchConfigDefaults.UCB1_EXPLORATION_FACTOR.value
        )
        strength_weight = (
            strength_conf.value_float
            if strength_conf and strength_conf.value_float is not None
            else SearchConfigDefaults.STRENGTH_WEIGHT.value
        )

        return UCB1ConfigDTO(
            total_display_count=total_display_count,
            exploration_factor=exploration_factor,
            strength_weight=strength_weight,
        )

    def is_channel_indexed(self, channel_id: int) -> bool:
        """检查频道ID是否已索引。"""
        return channel_id in self.indexed_channel_ids

    def get_indexed_channels(self, guild_id: int | None = None) -> list[discord.ForumChannel]:
        """获取已索引的频道对象列表。可选按 guild_id 过滤。"""
        if guild_id is not None:
            guild_data = self.guild_channels.get(guild_id, {})
            return list(guild_data.values())
        return list(self.indexed_channels.values())

    def get_indexed_channel_ids_set(self, guild_id: int | None = None) -> set[int]:
        """从缓存中获取已索引的频道ID集合。可选按 guild_id 过滤。"""
        if guild_id is not None:
            return set(self.guild_channels.get(guild_id, {}).keys())
        return self.indexed_channel_ids

    def get_indexed_channel_ids_list(self, guild_id: int | None = None) -> list[int]:
        """从缓存中获取已索引的频道ID列表。可选按 guild_id 过滤。"""
        return list(self.get_indexed_channel_ids_set(guild_id))
