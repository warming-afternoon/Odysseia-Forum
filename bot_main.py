import sys

if sys.platform != "win32":
    try:
        import uvloop

        uvloop.install()
        print("uvloop 已启用。")
    except ImportError:
        print("未找到 uvloop，将使用默认的 asyncio 事件循环。")


import json
import discord
import logging
from discord.ext import commands
import asyncio
import uvicorn

from shared.database import AsyncSessionFactory, init_db, close_db
from shared.redis_client import RedisManager
from ThreadManager.cog import ThreadManager
from core.tag_cache_service import TagCacheService
from core.cache_service import CacheService
from core.sync_service import SyncService
from core.impression_cache_service import ImpressionCacheService
from indexer.cog import Indexer
from search.cog import Search
from preferences.cog import Preferences
from auditor.cog import Auditor
from config.cog import Configuration
from banner.cog import BannerManagement
from core.config_repository import ConfigRepository
from collection.cog import CollectionCog
from update_detector.cog import UpdateDetector
from shared.api_scheduler import APIScheduler
from shared.enum.search_config_type import SearchConfigDefaults, SearchConfigDefaultsInt
from api.v1.routers import (
    preferences as preferences_api,
    search as search_api,
    meta as meta_api,
    fetch_images as fetch_images_api,
    banner as banner_api,
    tags as tags_api,
    discovery as discovery_api,
    booklists as booklists_api,
)
from api.main import app as fastapi_app
from api.v1.dependencies.security import initialize_api_security
from api.v1.routers.auth import initialize_auth_config

logger = logging.getLogger(__name__)


class MyBot(commands.Bot):
    def __init__(self, *, intents: discord.Intents, config: dict):
        proxy = config.get("proxy")
        bot_kwargs = {"command_prefix": "!", "intents": intents}
        if proxy:
            bot_kwargs["proxy"] = proxy
        super().__init__(**bot_kwargs)

        self.config = config
        self.db_url = config["db_url"]
        self.tag_cache_service: TagCacheService
        self.cache_service: CacheService
        self.sync_service: SyncService
        self.impression_cache_service: ImpressionCacheService

        # 从配置初始化API调度器
        concurrency = self.config.get("performance", {}).get(
            "api_scheduler_concurrency", 40
        )
        self.api_scheduler = APIScheduler(concurrent_requests=concurrency)

    async def process_commands(self, message: discord.Message):
        """
        重写此方法以阻止机器人处理任何文本命令。
        这可以防止因其他机器人的命令而产生 CommandNotFound 错误。
        """
        return  # 什么都不做

    async def on_index_updated_global(self):
        """接收 'index_updated' 事件并刷新缓存"""
        logger.debug("接收 'index_updated' 事件，开始刷新缓存")
        tasks = []
        if self.tag_cache_service:
            tasks.append(self.tag_cache_service.build_cache())
        if self.cache_service:
            tasks.append(self.cache_service.build_or_refresh_cache())

        if tasks:
            await asyncio.gather(*tasks)
        logger.info("核心缓存刷新完毕")

    def reload_config(self):
        """重新加载配置文件"""
        try:
            with open("config.json", "r", encoding="utf-8") as f:
                new_config = json.load(f)

            # 更新配置
            self.config = new_config

            # 更新API调度器并发数
            concurrency = self.config.get("performance", {}).get(
                "api_scheduler_concurrency", 40
            )
            self.api_scheduler.update_concurrency(concurrency)

            logger.info("配置文件重载成功")
            return True, "配置重载成功"

        except Exception as e:
            logger.error(f"重载配置文件失败: {e}", exc_info=True)
            return False, f"配置重载失败: {e}"

    async def setup_hook(self):
        """在机器人登录前执行的初始化。"""
        # 启动API调度器
        self.api_scheduler.start()
        await init_db()

        main_guild_id = self._get_main_guild_id_from_config()

        # 确保搜索配置存在
        async with AsyncSessionFactory() as session:
            config_repository = ConfigRepository(session)
            await config_repository.initialize_search_configs(main_guild_id)

        # 1. 初始化核心服务
        self.tag_cache_service = TagCacheService(AsyncSessionFactory)
        self.cache_service = CacheService(self, AsyncSessionFactory)
        self.sync_service = SyncService(
            bot=self,
            session_factory=AsyncSessionFactory,
        )
        self.impression_cache_service = ImpressionCacheService(
            bot=self, session_factory=AsyncSessionFactory
        )
        self.impression_cache_service.start()

        # 并行构建缓存
        await asyncio.gather(
            self.tag_cache_service.build_cache(),
            self.cache_service.build_or_refresh_cache(),
        )

        # 2. 加载 Cogs
        cogs_to_load = [
            ThreadManager(
                bot=self,
                session_factory=AsyncSessionFactory,
                config=self.config,
            ),
            Indexer(
                bot=self,
                session_factory=AsyncSessionFactory,
                config=self.config,
            ),
            Search(
                bot=self,
                session_factory=AsyncSessionFactory,
                config=self.config,
            ),
            Preferences(
                bot=self,
                session_factory=AsyncSessionFactory,
                config=self.config,
            ),
            Auditor(
                bot=self,
                session_factory=AsyncSessionFactory,
            ),
            Configuration(
                bot=self,
                session_factory=AsyncSessionFactory,
            ),
            BannerManagement(
                bot=self,
                session_factory=AsyncSessionFactory,
            ),
            CollectionCog(
                bot=self,
                session_factory=AsyncSessionFactory,
            ),
            UpdateDetector(
                bot=self,
                session_factory=AsyncSessionFactory,
                config=self.config,
            ),
        ]
        await asyncio.gather(
            *(self.add_cog(cog) for cog in cogs_to_load), return_exceptions=True
        )
        logger.info("所有 Cogs 已加载。")

        # 3. 注册全局事件监听器
        self.add_listener(self.on_index_updated_global, "on_index_updated")

        # 4. 注入 API 路由依赖
        self._inject_api_dependencies()

        # --- 同步应用程序命令 ---
        try:
            synced = await self.tree.sync()
            logger.info(f"成功同步 {len(synced)} 个应用程序命令。")
        except Exception as e:
            logger.error(f"同步应用程序命令时出错: {e}", exc_info=True)

    async def close(self):
        """关闭机器人时，一并关闭调度器和数据库连接。"""
        await self.impression_cache_service.stop()
        await self.api_scheduler.stop()
        await close_db()
        await super().close()

    # -------------------------
    # 辅助方法
    # -------------------------

    def _inject_api_dependencies(self):
        """向 API 路由模块注入运行期依赖"""
        
        # 获取主服务器ID
        main_guild_id = self._get_main_guild_id_from_config()
        
        # 注入服务实例到 API 路由
        preferences_api.async_session_factory = AsyncSessionFactory
        preferences_api.main_guild_id = main_guild_id

        meta_api.cache_service_instance = self.cache_service

        search_api.async_session_factory = AsyncSessionFactory
        search_api.cache_service_instance = self.cache_service
        search_api.tag_cache_service_instance = self.tag_cache_service
        search_api.impression_cache_service_instance = self.impression_cache_service

        tags_api.async_session_factory = AsyncSessionFactory
        tags_api.cache_service_instance = self.cache_service

        discovery_api.async_session_factory = AsyncSessionFactory
        discovery_api.main_guild_id = main_guild_id

        banner_api.async_session_factory = AsyncSessionFactory
        banner_api.banner_config = self.config.get("banner", {})
        banner_api.bot_instance = self

        # 注入频道映射配置
        channel_mappings_config = self._build_channel_mappings_config()
        search_api.channel_mappings_config = channel_mappings_config
        meta_api.channel_mappings_config = channel_mappings_config
        tags_api.channel_mappings_config = channel_mappings_config
        booklists_api.channel_mappings_config = channel_mappings_config

        auth_section = self.config.get("auth", {}) if isinstance(self.config, dict) else {}
        fetch_images_api.configure_fetch_images_router(
            session_factory=AsyncSessionFactory,
            bot_token=auth_section.get("bot_token"),
            guild_id=auth_section.get("guild_id"),
        )

        logger.info("API 路由服务注入完成")

    def _build_channel_mappings_config(self) -> dict[int, list[dict]]:
        """将配置中的频道映射转换为 API 路由可直接使用的结构"""
        raw_mappings = self.config.get("channel_mappings", {})
        parsed_mappings = {}
        for key, val in raw_mappings.items():
            if key.startswith("_"):
                continue

            if not isinstance(val, list):
                continue

            try:
                ch_id = int(key)
            except (ValueError, TypeError):
                continue

            parsed_mappings[ch_id] = [
                {
                    "tag_name": mapping["tag_name"],
                    "source_channel_ids": [
                        int(channel_id)
                        for channel_id in mapping.get("source_channel_ids", [])
                    ],
                }
                for mapping in val
                if isinstance(mapping, dict) and "tag_name" in mapping
            ]
        return parsed_mappings

    def _get_main_guild_id_from_config(self) -> int:
        """从配置文件读取主服务器 ID；为空时回退到默认值。"""
        raw_main_guild_id = self.config.get("main_guild_id")
        if raw_main_guild_id in (None, ""):
            return int(SearchConfigDefaultsInt.MAIN_GUILD_ID.value)

        try:
            return int(raw_main_guild_id)
        except (TypeError, ValueError):
            logger.warning(
                "config.json 中的 main_guild_id 无法解析，已回退到默认主服务器 ID。"
            )
            return int(SearchConfigDefaultsInt.MAIN_GUILD_ID.value)



async def main():
    # 配置日志记录
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True
    intents.reactions = True

    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)
        
    # 读取配置项并初始化全局Redis连接池
    redis_url = config.get("redis_url", "redis://odysseia-redis:6379/0")
    await RedisManager.init_redis(redis_url)

    bot = MyBot(intents=intents, config=config)

    @bot.event
    async def on_ready():
        if bot.user:
            logger.info(f"机器人已登录: {bot.user} (ID: {bot.user.id})")
        else:
            logger.info("机器人已登录，但无法获取机器人信息。")


    # 初始化 API 安全配置和认证配置
    initialize_api_security()
    initialize_auth_config()

    # 配置并并行运行 Bot 和 API 服务器
    api_config = config.get("api", {})
    uvicorn_config = uvicorn.Config(
        app=fastapi_app,
        host=api_config.get("host", "0.0.0.0"),
        port=api_config.get("port", 10810),
        log_level="warning",
        ssl_keyfile=api_config.get("ssl_key_path", None)
        if api_config.get("enable_ssl", False)
        else None,
        ssl_certfile=api_config.get("ssl_cert_path", None)
        if api_config.get("enable_ssl", False)
        else None,
    )
    server = uvicorn.Server(uvicorn_config)

    async with bot:
        await asyncio.gather(bot.start(config["token"]), server.serve())
        
    # 服务关闭时切断与Redis的连接
    await RedisManager.close_redis()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("机器人关闭。")
