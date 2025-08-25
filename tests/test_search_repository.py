# tests/test_search_repository.py

import pytest
import pytest_asyncio
import asyncio
from typing import AsyncGenerator
from datetime import datetime

from sqlalchemy.pool import StaticPool
from sqlalchemy import event  # <--- 修正 1: 导入 event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlmodel import SQLModel, text

# 调整 sys.path 以便能够导入 src 目录下的模块
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

# 从项目中导入必要的模块
from shared.fts5_tokenizer import register_jieba_tokenizer
from shared.models.thread import Thread
from search.repository import SearchRepository
from search.qo.thread_search import ThreadSearchQuery
from tag_system.tagService import TagService

# 使用内存数据库进行测试
TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"


@pytest_asyncio.fixture(scope="function")
async def db_session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    engine = create_async_engine(
        TEST_DATABASE_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def on_connect(dbapi_conn, connection_record):
        try:
            # aiosqlite > 0.17.0
            aiosqlite_conn = dbapi_conn._connection
            underlying_sqlite3_conn = aiosqlite_conn._conn
            register_jieba_tokenizer(underlying_sqlite3_conn)
        except Exception as e:
            print(f"在新连接上注册分词器失败: {e}")
            raise

    # 将数据库初始化逻辑（包括FTS表和触发器）放在 fixture 内部
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # <--- 修正 2: 补全缺失的 FTS 和触发器创建 SQL
        await conn.execute(
            text(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS thread_fts USING fts5(
                    title,
                    first_message_excerpt,
                    content='thread',
                    content_rowid='id',
                    tokenize = 'jieba'
                );
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS thread_after_insert
                AFTER INSERT ON thread BEGIN
                    INSERT INTO thread_fts(rowid, title, first_message_excerpt)
                    VALUES (new.id, new.title, new.first_message_excerpt);
                END;
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS thread_after_delete
                AFTER DELETE ON thread BEGIN
                    INSERT INTO thread_fts(thread_fts, rowid, title, first_message_excerpt)
                    VALUES ('delete', old.id, old.title, old.first_message_excerpt);
                END;
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS thread_after_update
                AFTER UPDATE ON thread BEGIN
                    INSERT INTO thread_fts(thread_fts, rowid, title, first_message_excerpt)
                    VALUES ('delete', old.id, old.title, old.first_message_excerpt);
                    INSERT INTO thread_fts(rowid, title, first_message_excerpt)
                    VALUES (new.id, new.title, new.first_message_excerpt);
                END;
                """
            )
        )

    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    yield factory
    
    await engine.dispose()

@pytest_asyncio.fixture(scope="function")
async def seeded_db_session(db_session_factory: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession, None]: # <--- 修正 3: 修正返回类型注解
    """
    提供一个已经填充了测试数据的数据库会话。
    """
    async with db_session_factory() as session:
        threads_to_create = [
            Thread(channel_id=1, thread_id=101, title="关于百合破坏的讨论", author_id=1, created_at=datetime.now(), first_message_excerpt="这是一个关于百合破坏的话题"),
            Thread(channel_id=1, thread_id=102, title="🈲百合破坏", author_id=2, created_at=datetime.now(), first_message_excerpt="帖子内容"),
            Thread(channel_id=1, thread_id=103, title="小说推荐", author_id=3, created_at=datetime.now(), first_message_excerpt="推荐一些好看的小说"),
            Thread(channel_id=1, thread_id=104, title="禁：请勿讨论百合破坏话题", author_id=4, created_at=datetime.now(), first_message_excerpt="社区规则，禁止讨论百合破坏"),
        ]
        session.add_all(threads_to_create)
        await session.commit()
        yield session

@pytest.mark.asyncio
async def test_search_with_excluded_keywords_and_exemptions(
    seeded_db_session: AsyncSession, 
    db_session_factory: async_sessionmaker[AsyncSession]
):
    """
    测试反选关键词和豁免逻辑。
    """
    # 1. 准备
    tag_service = TagService(session_factory=db_session_factory)
    await tag_service.build_cache()
    
    repo = SearchRepository(session=seeded_db_session, tag_service=tag_service)

    # 2. 构建查询
    query = ThreadSearchQuery(
        exclude_keywords="百合破坏",
        exclude_keyword_exemption_markers=["禁", "🈲"]
    )
    
    # 3. 执行搜索 (需要先在 repository.py 中应用 bug 修复)
    threads, total_threads = await repo.search_threads_with_count(query, offset=0, limit=10)

    # 4. 断言结果
    assert total_threads == 3
    returned_titles = {t.title for t in threads}
    
    assert "关于百合破坏的讨论" not in returned_titles
    assert "🈲百合破坏" in returned_titles
    assert "小说推荐" in returned_titles
    assert "禁：请勿讨论百合破坏话题" in returned_titles