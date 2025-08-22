try:
    import uvloop
    uvloop.install()
    print("uvloop is enabled.")
except ImportError:
    print("uvloop not found, using default asyncio event loop.")


import json
import discord
import logging
from discord.ext import commands
import asyncio
from typing import cast

from shared.database import AsyncSessionFactory, init_db, close_db
from tag_system.cog import TagSystem
from tag_system.tagService import TagService
from indexer.cog import Indexer
from search.cog import Search
from auditor.cog import Auditor
from shared.api_scheduler import APIScheduler

logger = logging.getLogger(__name__)


class MyBot(commands.Bot):
    def __init__(self, *, intents: discord.Intents, config: dict):
        proxy = config.get("proxy")
        bot_kwargs = {"command_prefix": "/", "intents": intents}
        if proxy:
            bot_kwargs["proxy"] = proxy
        super().__init__(**bot_kwargs)

        self.config = config
        self.db_url = config["db_url"]
        self.tag_service: TagService | None = None

        # 从配置初始化API调度器
        concurrency = self.config.get("performance", {}).get(
            "api_scheduler_concurrency", 40
        )
        self.api_scheduler = APIScheduler(concurrent_requests=concurrency)

    async def setup_hook(self):
        """在机器人登录前执行的异步初始化。"""
        # 启动API调度器
        self.api_scheduler.start()
        await init_db()

        self.tag_service = TagService(AsyncSessionFactory)
        tag_system_cog = TagSystem(
            bot=self, session_factory=AsyncSessionFactory, config=self.config
        )
        await asyncio.gather(
            self.tag_service.build_cache(), self.add_cog(tag_system_cog)
        )

        retrieved_tag_system_cog = self.get_cog("TagSystem")
        if retrieved_tag_system_cog and self.tag_service:
            dependent_cogs = [
                Indexer(
                    bot=self,
                    session_factory=AsyncSessionFactory,
                    config=self.config,
                    tag_service=self.tag_service,
                ),
                Search(
                    bot=self,
                    session_factory=AsyncSessionFactory,
                    config=self.config,
                    tag_service=self.tag_service,
                ),
                Auditor(
                    bot=self,
                    session_factory=AsyncSessionFactory,
                    api_scheduler=self.api_scheduler,
                    tag_system_cog=cast(TagSystem, retrieved_tag_system_cog),
                ),
            ]
            await asyncio.gather(*(self.add_cog(cog) for cog in dependent_cogs))
        else:
            logger.error(
                "无法加载依赖性 Cogs，因为 TagService 或 TagSystem 未能成功加载。"
            )

        # --- 同步应用程序命令 ---
        try:
            synced = await self.tree.sync()
            logger.info(f"成功同步 {len(synced)} 个应用程序命令。")
        except Exception as e:
            logger.error(f"同步应用程序命令时出错: {e}", exc_info=True)

    async def close(self):
        """关闭机器人时，一并关闭调度器和数据库连接。"""
        await self.api_scheduler.stop()
        await close_db()
        await super().close()


async def main():
    # 配置日志记录
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True
    intents.reactions = True

    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    bot = MyBot(intents=intents, config=config)

    @bot.event
    async def on_ready():
        if bot.user:
            logger.info(f"机器人已登录: {bot.user} (ID: {bot.user.id})")
        else:
            logger.info("机器人已登录，但无法获取机器人信息。")

    async with bot:
        await bot.start(config["token"])


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("机器人关闭。")
