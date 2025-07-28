import os
from typing import AsyncGenerator
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import text

# 导入所有模型，以便 SQLModel 可以发现它们并创建表
from .models.thread import Thread
from .models.tag import Tag
from .models.thread_tag_link import ThreadTagLink
from .models.user_search_preferences import UserSearchPreferences
from .models.tag_vote import TagVote

DB_PATH = "data/database.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# 创建异步数据库引擎
async_engine = create_async_engine(DATABASE_URL, echo=False)

# 创建异步会话工厂
AsyncSessionFactory = sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def init_db():
    """
    初始化数据库，创建所有在 SQLModel.metadata 中注册的表
    """
    # 确保数据库文件所在的目录存在
    db_dir = os.path.dirname(DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)

    async with async_engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    获取一个新的数据库会话。
    这是一个依赖项，可以被 FastAPI 或其他框架注入。
    """
    async with AsyncSessionFactory() as session:
        yield session

async def close_db():
    """
    关闭数据库引擎，释放连接池。
    """
    print("正在关闭数据库连接池...")
    await async_engine.dispose()
    print("数据库连接池已关闭。")