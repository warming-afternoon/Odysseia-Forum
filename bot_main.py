import json
import discord
from discord.ext import commands
import asyncio

# 新的架构组件
from shared.database import Database
from tag_system.repository import TagSystemRepository
from search.repository import SearchRepository
from tag_system.cog import TagSystem
from indexer.cog import Indexer
from search.cog import Search
from core.api_scheduler import APIScheduler

class MyBot(commands.Bot):
    def __init__(self, *, intents: discord.Intents, db_url: str):
        super().__init__(command_prefix="/", intents=intents)
        self.db_url = db_url
        # 初始化API调度器
        self.api_scheduler = APIScheduler(concurrent_requests=40)

    async def setup_hook(self):
        """在机器人登录前执行的异步初始化。"""
        # 启动API调度器
        self.api_scheduler.start()

        # 初始化数据库和仓库
        db = Database(self.db_url)
        await db.init_db()
        tag_system_repo = TagSystemRepository(db)
        search_repo = SearchRepository(db)

        # 加载 Cogs 并注入依赖项
        # 注意：现在 cog 可以通过 self.bot.api_scheduler 访问调度器
        await self.add_cog(TagSystem(self, tag_system_repo))
        await self.add_cog(Indexer(self, tag_system_repo))
        await self.add_cog(Search(self, search_repo, tag_system_repo))

        # 同步应用程序命令
        try:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} commands")
        except Exception as e:
            print(f"Failed to sync commands: {e}")

    async def close(self):
        """关闭机器人时，优雅地关闭调度器。"""
        await self.api_scheduler.stop()
        await super().close()

async def main():
    intents = discord.Intents.default()
    intents.message_content = True
    intents.guilds = True
    intents.members = True
    intents.reactions = True

    with open("config.json", "r", encoding="utf-8") as f:
        config = json.load(f)

    bot = MyBot(intents=intents, db_url=config["db_url"])

    @bot.event
    async def on_ready():
        print(f"Logged in as {bot.user} (ID: {bot.user.id})")

    async with bot:
        await bot.start(config["token"])

if __name__ == "__main__":
    asyncio.run(main())