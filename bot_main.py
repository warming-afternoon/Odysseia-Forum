import json
import discord
import logging
from discord.ext import commands
import asyncio
# import uvloop
import orjson


from shared.database import AsyncSessionFactory, init_db, close_db
from tag_system.cog import TagSystem
from tag_system.tagService import TagService
from indexer.cog import Indexer
from search.cog import Search
from shared.models.api_scheduler import APIScheduler

class MyBot(commands.Bot):
    def __init__(self, *, intents: discord.Intents, config: dict):
        proxy = config.get("proxy")
        super().__init__(command_prefix="/", intents=intents, proxy=proxy, json_impl=orjson)
        self.config = config
        self.db_url = config["db_url"]
        self.tag_service: TagService | None = None
        
        # 从配置初始化API调度器
        concurrency = self.config.get("performance", {}).get("api_scheduler_concurrency", 40)
        self.api_scheduler = APIScheduler(concurrent_requests=concurrency)

    async def setup_hook(self):
        """在机器人登录前执行的异步初始化。"""
        # 启动API调度器
        self.api_scheduler.start()

        # 初始化数据库
        await init_db()

        # 初始化并缓存 TagService
        async with AsyncSessionFactory() as session:
            self.tag_service = TagService(session)
            await self.tag_service.build_cache()

        # 加载 Cogs 并注入依赖
        await self.add_cog(TagSystem(bot=self, session_factory=AsyncSessionFactory, config=self.config))
        await self.add_cog(Indexer(bot=self, session_factory=AsyncSessionFactory, config=self.config, tag_service=self.tag_service))
        await self.add_cog(Search(bot=self, session_factory=AsyncSessionFactory, config=self.config, tag_service=self.tag_service))

        # 同步应用程序命令
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} commands")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

    async def close(self):
        """关闭机器人时，一并关闭调度器和数据库连接。"""
        await self.api_scheduler.stop()
        await close_db()
        await super().close()

async def main():
    # 配置日志记录
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
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
        print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    async with bot:
        await bot.start(config["token"])

if __name__ == "__main__":
    try:
        # uvloop.install()
        asyncio.run(main())
    except KeyboardInterrupt:
        print("机器人关闭。")