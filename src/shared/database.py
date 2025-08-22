import os
from typing import AsyncGenerator
from sqlmodel import SQLModel
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

# 导入所有模型，以便 SQLModel 可以发现它们并创建表

DB_PATH = "data/database.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# 创建异步数据库引擎
async_engine = create_async_engine(DATABASE_URL, echo=False)

# 创建异步会话工厂
AsyncSessionFactory = async_sessionmaker(
    bind=async_engine,
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

async def close_db():
    """
    关闭数据库引擎，释放连接池。
    """
    print("正在关闭数据库连接池...")
    await async_engine.dispose()
    print("数据库连接池已关闭。")
