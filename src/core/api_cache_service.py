import asyncio
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import asdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.api_channel_metadata_service import ApiChannelMetadataService
from core.booklist_item_repository import BooklistItemRepository
from core.config_repository import ConfigRepository
from core.null_bot import NullBot
from core.tag_cache_service import TagCacheService
from dto.bot_config_dto import BotConfigDTO
from dto.meta.channel_meta import ChannelMeta
from dto.search import SearchConfigDTO
from shared.enum import CacheKeys, SearchConfigDefaults, SearchConfigType

logger = logging.getLogger(__name__)


class ApiCacheService:
    """
    API 进程从数据库独立构建缓存，不依赖 BOT 进程或其 Redis 快照。
    """

    def __init__(
        self,
        session_factory: async_sessionmaker,
        redis_client,
        tag_cache_service: TagCacheService | None = None,
    ):
        """复用数据库和 Redis，并统一管理 API 使用的标签缓存。"""
        self.bot = NullBot()
        self.session_factory = session_factory
        self._redis = redis_client
        self.tag_cache_service = tag_cache_service or TagCacheService(session_factory)
        self.indexed_channel_ids: set[int] = set()
        # int -> ChannelMeta
        self.indexed_channels: dict[int, ChannelMeta] = {}
        # guild_id -> {channel_id -> ChannelMeta}
        self.guild_channels: dict[int, dict[int, ChannelMeta]] = {}
        self.bot_configs: dict[SearchConfigType, BotConfigDTO] = {}
        self._task: asyncio.Task | None = None
        self._is_running = False
        self._refresh_lock = asyncio.Lock()
        self.is_ready = False
        self.last_refresh_succeeded = False
        self.metadata_version = ""
        logger.debug("ApiCacheService 已初始化")

    # ── 公开接口（与 CacheService 完全一致）─────────────────────

    async def build_cache(self):
        """首次构建缓存。"""
        await self.refresh_cache()
        logger.info(
            f"ApiCacheService 缓存构建完毕。"
            f"已索引 {len(self.indexed_channel_ids)} 个频道，"
            f"加载了 {len(self.indexed_channels)} 个频道元数据，"
            f"缓存了 {len(self.bot_configs)} 个 BotConfig 配置项。"
        )

    def start(self):
        """启动定时同步后台任务。"""
        if self._is_running:
            return
        self._is_running = True
        self._task = asyncio.create_task(self._periodic_sync())
        logger.info("ApiCacheService 定时同步已启动（每 60 秒一次）")

    async def stop(self):
        """停止定时同步任务。"""
        if not self._is_running:
            return
        self._is_running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("ApiCacheService 定时同步已停止")

    async def get_bot_config(
        self, config_type: SearchConfigType
    ) -> BotConfigDTO | None:
        """从缓存获取配置；如未命中则自动刷新后重试。"""
        config = self.bot_configs.get(config_type)
        if config is not None:
            return config

        logger.info(f"ApiCacheService 配置缓存未命中: {config_type.name}，正在刷新...")
        await self.refresh_bot_config_cache()
        config = self.bot_configs.get(config_type)
        if config is None:
            logger.error(f"刷新缓存后仍然找不到配置: {config_type.name}.")
        return config

    async def get_ucb1_config(self) -> SearchConfigDTO:
        """获取搜索算法所需的配置参数。"""
        total_disp_conf = await self.get_bot_config(
            SearchConfigType.TOTAL_DISPLAY_COUNT
        )
        ucb_factor_conf = await self.get_bot_config(
            SearchConfigType.UCB1_EXPLORATION_FACTOR
        )
        strength_conf = await self.get_bot_config(SearchConfigType.STRENGTH_WEIGHT)
        reddit_hot_conf = await self.get_bot_config(
            SearchConfigType.REDDIT_HOT_TIME_DECAY
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
        reddit_hot_time_decay = (
            reddit_hot_conf.value_float
            if reddit_hot_conf and reddit_hot_conf.value_float is not None
            else SearchConfigDefaults.REDDIT_HOT_TIME_DECAY.value
        )

        return SearchConfigDTO(
            total_display_count=total_display_count,
            exploration_factor=exploration_factor,
            strength_weight=strength_weight,
            reddit_hot_time_decay=reddit_hot_time_decay,
        )

    async def refresh_bot_config_cache(self):
        """从数据库刷新 BotConfig 缓存。"""
        async with self._refresh_lock:
            async with self.session_factory() as session:
                configs = await ConfigRepository(
                    session
                ).get_all_configurable_search_configs()
                # 在会话关闭前复制 ORM 字段，避免过期对象触发隐式查询。
                bot_configs = {
                    SearchConfigType(item.type): BotConfigDTO.from_orm(item)
                    for item in configs
                    if item.type in SearchConfigType._value2member_map_
                }
            self.bot_configs = bot_configs

    def is_channel_indexed(self, channel_id: int) -> bool:
        """检查频道ID是否已索引。"""
        return channel_id in self.indexed_channel_ids

    def get_indexed_channels(self, guild_id: int | None = None) -> list[ChannelMeta]:
        """获取已索引的频道元数据列表。可选按 guild_id 过滤。"""
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

    async def get_tournament_info_batch(
        self, session, thread_ids: list[int]
    ) -> dict[int, list[dict]]:
        """
        批量获取 thread_id → [TournamentInfo] 映射。

        优先 Redis MGET，未命中则 DB 补查并回填。
        Redis Key: ``tournament:thread:{thread_id}``, TTL 1h。
        空结果也缓存（"[]"），避免反复穿透。
        """
        if not thread_ids:
            return {}

        keys = [CacheKeys.TOURNAMENT_THREAD.format(thread_id=tid) for tid in thread_ids]
        result: dict[int, list[dict]] = {}
        miss_ids: list[int] = []

        # ── MGET 批量读 ──
        try:
            cached = await self._redis.mget(keys)
            for tid, val in zip(thread_ids, cached):
                if val:
                    result[tid] = json.loads(val)
                else:
                    miss_ids.append(tid)
        except Exception:
            logger.warning("Redis MGET 赛事缓存失败，降级全走 DB", exc_info=True)
            miss_ids = thread_ids

        # ── DB 补查未命中 ──
        if miss_ids:
            repo = BooklistItemRepository(session)
            db_map = await repo.get_tournament_info_by_thread_ids(miss_ids)

            # ── 回填 Redis ──
            # 每批最多一百条，避免逐帖往返或单个超大 pipeline 占用内存。
            for start in range(0, len(miss_ids), 100):
                try:
                    async with self._redis.pipeline(transaction=False) as pipeline:
                        for tid in miss_ids[start : start + 100]:
                            infos = db_map.get(tid, [])
                            key = CacheKeys.TOURNAMENT_THREAD.format(thread_id=tid)
                            pipeline.setex(
                                key, 3600, json.dumps(infos) if infos else "[]"
                            )
                        await pipeline.execute()
                except Exception:
                    pass

            result.update(db_map)

        return result

    # ── 内部方法 ─────────────────────────────────────────────

    async def refresh_cache(self):
        """在一致性只读事务中构建完整缓存，成功后统一替换旧快照。"""
        async with self._refresh_lock:
            try:
                async with self.session_factory() as session, session.begin():
                    # 同一轮读取共享数据库快照，不将并发更新前后的资料混在一起。
                    await session.connection(
                        execution_options={"isolation_level": "REPEATABLE READ"}
                    )
                    await session.execute(text("SET TRANSACTION READ ONLY"))
                    indexed_ids, channels = await ApiChannelMetadataService(
                        session
                    ).load_channels()
                    configs = await ConfigRepository(
                        session
                    ).get_all_configurable_search_configs()
                    bot_configs = {
                        SearchConfigType(item.type): BotConfigDTO.from_orm(item)
                        for item in configs
                        if item.type in SearchConfigType._value2member_map_
                    }
                    tag_snapshot = await self.tag_cache_service.load_snapshot(session)
                guild_channels: dict[int, dict[int, ChannelMeta]] = defaultdict(dict)
                for channel in channels.values():
                    guild_channels[channel.guild.id][channel.id] = channel
                # 仅目录内容决定版本；同步时间变化不会反复制造缓存键。
                encoded = json.dumps(
                    [asdict(channels[key]) for key in sorted(channels)],
                    sort_keys=True,
                    ensure_ascii=False,
                ).encode("utf-8")
                version = hashlib.sha256(encoded).hexdigest()[:16]
            except Exception:
                self.last_refresh_succeeded = False
                raise
            # 以下替换没有异步等待，读取失败时任何已发布缓存均保持原状。
            self.tag_cache_service.apply_snapshot(tag_snapshot)
            self.indexed_channel_ids = indexed_ids
            self.indexed_channels = channels
            self.guild_channels = dict(guild_channels)
            self.bot_configs = bot_configs
            self.metadata_version = version
            self.is_ready = True
            self.last_refresh_succeeded = True

    async def _periodic_sync(self):
        """每六十秒刷新数据库频道、标签和搜索配置，失败保留旧缓存。"""
        while self._is_running:
            await asyncio.sleep(60)
            try:
                logger.debug("ApiCacheService 定时同步触发")
                await self.refresh_cache()
            except Exception:
                logger.exception("ApiCacheService 定时同步失败，保留已有缓存")
