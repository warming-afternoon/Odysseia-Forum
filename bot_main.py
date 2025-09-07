import asyncio
import json
import discord
from discord.ext import commands

from cogs.tag_system import TagSystem
from cogs.indexer import Indexer
from cogs.search import Search
import database

intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True
intents.members = True
intents.reactions = True

with open("config.json", "r", encoding="utf-8") as f:
    config = json.load(f)

bot = commands.Bot(command_prefix="/", intents=intents)


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    await database.init_db()

    await bot.add_cog(TagSystem(bot))
    await bot.add_cog(Indexer(bot))
    await bot.add_cog(Search(bot))
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Failed to sync commands: {e}")

bot.run(config["token"])