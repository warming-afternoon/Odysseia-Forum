from typing import AsyncGenerator
from sqlmodel import SQLModel, create_async_engine
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

# 导入所有模型，以便 SQLModel 可以发现它们并创建表
from .models.thread import Thread
from .models.tag import Tag
from .models.thread_tag_link import ThreadTagLink
from .models.user_search_preferences import UserSearchPreferences

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

async def init_db():
    """
    初始化数据库，创建所有在 SQLModel.metadata 中注册的表。
    """
    async with async_engine.begin() as conn:
        # await conn.run_sync(SQLModel.metadata.drop_all) # 可选：在开发过程中用于清空数据库
        await conn.run_sync(SQLModel.metadata.create_all)

async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    获取一个新的数据库会话。
    这是一个依赖项，可以被 FastAPI 或其他框架注入。
    """
    async with AsyncSessionFactory() as session:
        yield session