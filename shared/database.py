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

DB_PATH = "forum_search.db"
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

# 创建异步数据库引擎
async_engine = create_async_engine(DATABASE_URL, echo=True)

# 创建异步会话工厂
AsyncSessionFactory = sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def migrate_database(conn):
    """
    执行数据库迁移，例如添加新列。
    """
    # 检查 threads 表是否有 tag_votes_summary 字段
    table_info = await conn.run_sync(
        lambda sync_conn: sync_conn.execute(
            text("PRAGMA table_info(thread)")
        ).fetchall()
    )
    column_names = [col[1] for col in table_info]
    
    if 'tag_votes_summary' not in column_names:
        print("正在为 'thread' 表添加 'tag_votes_summary' 字段...")
        await conn.execute(text("ALTER TABLE thread ADD COLUMN tag_votes_summary TEXT"))
        print("已添加 'tag_votes_summary' 字段。")

async def init_db():
    """
    初始化数据库，创建所有在 SQLModel.metadata 中注册的表，并执行迁移。
    """
    async with async_engine.begin() as conn:
        # 1. 创建所有不存在的表
        await conn.run_sync(SQLModel.metadata.create_all)
        
        # 2. 执行手动迁移
        await migrate_database(conn)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    获取一个新的数据库会话。
    这是一个依赖项，可以被 FastAPI 或其他框架注入。
    """
    async with AsyncSessionFactory() as session:
        yield session