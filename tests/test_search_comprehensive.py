"""
对现有 SQLite FTS5 搜索行为的全面测试覆盖。

此文件是 PG 迁移的行为基线——迁移后，这些测试的全部通过
即代表搜索行为与当前 FTS5 实现等价。

覆盖范围：
- FTS 分词行为（中文/英文/数字/URL/特殊字符/CJK 混合）
- FTS MATCH 查询（正选单/多词、AND/OR 组、精确引号匹配）
- 排除关键词（基本排除、多词 OR 排除、豁免标记 NEAR 逻辑）
- 搜索过滤器组合（时间、频道、作者、标签 + FTS 关键词）
- 排序算法（UCB1、Reddit Hot、按字段、收藏排序）
- 边界值（空关键词、无匹配、全排除、分页偏移）
"""

import pytest
import pytest_asyncio
from typing import AsyncGenerator, List, Set
from datetime import datetime, timedelta

from sqlalchemy.pool import StaticPool
from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlmodel import SQLModel, text, delete

import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from shared.fts5_tokenizer import register_jieba_tokenizer
from models import Thread, Author, Tag, ThreadTagLink
from search.search_service import SearchService
from search.qo.thread_search import ThreadSearchQuery
from core.tag_cache_service import TagCacheService

TEST_DATABASE_URL = "sqlite+aiosqlite:///:memory:"

# ──────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────


@pytest_asyncio.fixture(scope="module")
async def db_session_factory() -> AsyncGenerator[
    async_sessionmaker[AsyncSession], None
]:
    """模块级别的异步 SQLite 内存数据库引擎 + 会话工厂（含 FTS5 表与触发器）。"""
    engine = create_async_engine(
        TEST_DATABASE_URL,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine.sync_engine, "connect")
    def on_connect(dbapi_conn, connection_record):
        try:
            aiosqlite_conn = dbapi_conn._connection
            underlying_sqlite3_conn = aiosqlite_conn._conn
            register_jieba_tokenizer(underlying_sqlite3_conn)
        except Exception as e:
            print(f"在新连接上注册分词器失败: {e}")
            raise

    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        # FTS5 虚拟表
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
        # 触发器
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


# ──────────────────────────────────────────────
# 数据种子 fixtures
# ──────────────────────────────────────────────


@pytest_asyncio.fixture(scope="function")
async def empty_db_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """无数据的干净会话。"""
    async with db_session_factory() as session:
        yield session
        await session.execute(delete(Thread))
        await session.execute(delete(Author))
        await session.commit()


@pytest_asyncio.fixture(scope="function")
async def seeded_basic_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """种子：5 条基础中文帖子（用于 FTS 排除/豁免测试）。"""
    async with db_session_factory() as session:
        threads_to_create = [
            Thread(channel_id=1, thread_id=101, title="关于百合破坏的讨论", author_id=1, created_at=datetime.now()),
            Thread(channel_id=1, thread_id=102, title="🈲百合破坏", author_id=2, created_at=datetime.now()),
            Thread(channel_id=1, thread_id=103, title="小说推荐", author_id=3, created_at=datetime.now()),
            Thread(channel_id=1, thread_id=104, title="禁：请勿讨论百合破坏话题", author_id=4, created_at=datetime.now()),
            Thread(channel_id=1, thread_id=105, title="纯爱小说分享", author_id=5, created_at=datetime.now()),
        ]
        session.add_all(threads_to_create)
        await session.commit()
        yield session
        await session.execute(delete(Thread))
        await session.commit()


@pytest_asyncio.fixture(scope="function")
async def seeded_fts_varied_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """种子：多种文本类型，用于 FTS 分词行为验证。

    覆盖：纯中文、中英混合、数字、URL、特殊字符、CJK 混合、长文本。
    """
    async with db_session_factory() as session:
        now = datetime.now()
        threads = [
            Thread(thread_id=201, channel_id=1, title="福州十六中出了条神龙",
                   first_message_excerpt="Gemini 3.1 Pro Preview 测试模型", author_id=1, created_at=now),
            Thread(thread_id=202, channel_id=1, title="纯爱小说分享",
                   first_message_excerpt="推荐一些好玩的RPG游戏和视觉小说", author_id=2, created_at=now),
            Thread(thread_id=203, channel_id=1, title="搬运工汉化教程",
                   first_message_excerpt="https://discord.com/channels/1134557553011998840/1481100119632777237",
                   author_id=3, created_at=now),
            Thread(thread_id=204, channel_id=1, title="Hello 世界 123",
                   first_message_excerpt="CJK混合文本 test テスト 한국어", author_id=4, created_at=now),
            Thread(thread_id=205, channel_id=1, title="商业用途绝对不可以",
                   first_message_excerpt="二改大大方方改把我这条蛆带上就行", author_id=5, created_at=now),
        ]
        session.add_all(threads)
        await session.commit()
        yield session
        await session.execute(delete(Thread))
        await session.commit()


@pytest_asyncio.fixture(scope="function")
async def seeded_sorting_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """种子：用于排序算法测试的帖子。

    包含不同的 reaction_count / display_count / created_at 用于 UCB1 和 Reddit Hot。
    """
    async with db_session_factory() as session:
        now = datetime.now()
        threads = [
            Thread(thread_id=301, channel_id=1, title="高互动高曝光",
                   reaction_count=100, display_count=500, reply_count=30,
                   collection_count=10,
                   created_at=now - timedelta(days=30), last_active_at=now - timedelta(hours=1),
                   author_id=1),
            Thread(thread_id=302, channel_id=1, title="高互动低曝光新帖",
                   reaction_count=80, display_count=20, reply_count=15,
                   collection_count=5,
                   created_at=now - timedelta(days=1), last_active_at=now,
                   author_id=2),
            Thread(thread_id=303, channel_id=1, title="低互动高曝光老帖",
                   reaction_count=5, display_count=1000, reply_count=2,
                   collection_count=1,
                   created_at=now - timedelta(days=90), last_active_at=now - timedelta(days=60),
                   author_id=3),
            Thread(thread_id=304, channel_id=1, title="零曝光新帖",
                   reaction_count=50, display_count=0, reply_count=8,
                   collection_count=0,
                   created_at=now - timedelta(hours=2), last_active_at=now - timedelta(minutes=30),
                   author_id=4),
            Thread(thread_id=305, channel_id=1, title="中等互动中等曝光",
                   reaction_count=30, display_count=100, reply_count=10,
                   collection_count=3,
                   created_at=now - timedelta(days=10), last_active_at=now - timedelta(days=1),
                   author_id=5),
        ]
        session.add_all(threads)
        await session.commit()
        yield session
        await session.execute(delete(Thread))
        await session.commit()


@pytest_asyncio.fixture(scope="function")
async def seeded_filter_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """种子：用于过滤器组合测试的帖子（频道、作者、标签、时间）。"""
    async with db_session_factory() as session:
        now = datetime.now()

        # 清理残留数据（以防前一个 fixture 清理失败）
        await session.execute(delete(ThreadTagLink))
        await session.execute(delete(Thread))
        await session.execute(delete(Tag))
        await session.execute(delete(Author))
        await session.commit()

        # 创建作者
        authors = [
            Author(id=1, name="author_one", display_name="Author One"),
            Author(id=2, name="author_two", display_name="Author Two"),
        ]
        session.add_all(authors)

        # 创建标签
        tags = [
            Tag(id=1001, name="汉化"),
            Tag(id=1002, name="原创"),
            Tag(id=1003, name="RPG"),
        ]
        session.add_all(tags)

        threads = [
            Thread(thread_id=401, channel_id=10, guild_id=1, title="汉化RPG游戏推荐",
                   author_id=1, created_at=now - timedelta(days=5),
                   last_active_at=now - timedelta(hours=2), reaction_count=20),
            Thread(thread_id=402, channel_id=10, guild_id=1, title="原创汉化工具分享",
                   author_id=1, created_at=now - timedelta(days=3),
                   last_active_at=now - timedelta(hours=1), reaction_count=15),
            Thread(thread_id=403, channel_id=20, guild_id=1, title="百合RPG小说推荐",
                   author_id=2, created_at=now - timedelta(days=10),
                   last_active_at=now - timedelta(days=1), reaction_count=30),
            Thread(thread_id=404, channel_id=20, guild_id=2, title="纯爱原创故事",
                   author_id=2, created_at=now - timedelta(days=1),
                   last_active_at=now, reaction_count=5),
            Thread(thread_id=405, channel_id=10, guild_id=1, title="隐藏帖子不应出现",
                   author_id=1, created_at=now, reaction_count=0, show_flag=False),
            Thread(thread_id=406, channel_id=10, guild_id=1, title="未找到的帖子不应出现",
                   author_id=1, created_at=now, reaction_count=0, not_found_count=3),
        ]
        session.add_all(threads)
        await session.commit()

        # 手动建立标签关联
        for tid, tag_id in [(1, 1001), (1, 1003), (2, 1001), (2, 1002), (3, 1003), (4, 1002)]:
            session.add(ThreadTagLink(thread_id=tid, tag_id=tag_id))
        await session.commit()
        yield session
        await session.execute(delete(ThreadTagLink))
        await session.execute(delete(Thread))
        await session.execute(delete(Tag))
        await session.execute(delete(Author))
        await session.commit()


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────


def _make_tag_cache(session_factory: async_sessionmaker) -> TagCacheService:
    return TagCacheService(session_factory=session_factory)


async def _search(
    session: AsyncSession,
    tag_cache: TagCacheService,
    **query_kwargs,
) -> tuple[List[Thread], int]:
    """快捷搜索，返回 (threads, total_count)。"""
    query = ThreadSearchQuery(**query_kwargs)
    service = SearchService(session=session, tag_cache_service=tag_cache)
    return await service.search_threads_with_count(
        query=query,
        limit=50,
        offset=0,
        total_display_count=1000,
        exploration_factor=1.414,
        strength_weight=10.0,
    )


def _titles(threads: List[Thread]) -> Set[str]:
    return {t.title for t in threads}


# ══════════════════════════════════════════════
# 1. FTS 分词行为测试
# ══════════════════════════════════════════════


class TestFTSRjiebaTokenization:
    """验证 rjieba 分词对各种文本类型的切分结果。

    这些测试确保迁移到 PG 后，Python 端分词能产生与 FTS5 内部 jieba 分词器
    一致的行为。
    """

    async def _insert_and_search_keywords(
        self, session: AsyncSession, tag_cache: TagCacheService,
        title: str, first_message_excerpt: str, search_keywords: str,
    ) -> int:
        """插入一条帖子，用给定关键词搜索，返回匹配数。"""
        t = Thread(
            thread_id=9000, channel_id=1, title=title,
            first_message_excerpt=first_message_excerpt, author_id=1,
            created_at=datetime.now(),
        )
        session.add(t)
        await session.commit()

        query = ThreadSearchQuery(keywords=search_keywords)
        service = SearchService(session=session, tag_cache_service=tag_cache)
        _, total = await service.search_threads_with_count(
            query=query, limit=50, offset=0,
            total_display_count=1000, exploration_factor=1.414, strength_weight=10.0,
        )
        await session.delete(t)
        await session.commit()
        return total

    @pytest.mark.asyncio
    async def test_chinese_tokenization_basic(self, empty_db_session, db_session_factory):
        """中文基本分词：'搬运工' 应匹配包含该词的帖子。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        count = await self._insert_and_search_keywords(
            empty_db_session, tag_cache,
            title="搬运工汉化教程", first_message_excerpt="",
            search_keywords="搬运工",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_chinese_multi_char_name(self, empty_db_session, db_session_factory):
        """多字人名/地名：'福州十六中' 分词后应可部分匹配。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        count = await self._insert_and_search_keywords(
            empty_db_session, tag_cache,
            title="福州十六中出了条神龙", first_message_excerpt="",
            search_keywords="十六中",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_english_text_tokenization(self, empty_db_session, db_session_factory):
        """英文/数字混合文本：'Gemini 3.1' 应可被搜索。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        count = await self._insert_and_search_keywords(
            empty_db_session, tag_cache,
            title="测试", first_message_excerpt="Gemini 3.1 Pro Preview 测试模型",
            search_keywords="Gemini",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_url_tokenization(self, empty_db_session, db_session_factory):
        """URL 应被按符号切分，搜索 URL 片段应命中。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        count = await self._insert_and_search_keywords(
            empty_db_session, tag_cache,
            title="教程", first_message_excerpt="https://discord.com/channels/1134557553011998840",
            search_keywords="discord",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_cjk_mixed_text(self, empty_db_session, db_session_factory):
        """CJK 混合文本：中/日/韩多语种混合。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        count = await self._insert_and_search_keywords(
            empty_db_session, tag_cache,
            title="Hello 世界 123", first_message_excerpt="CJK混合文本 test テスト 한국어",
            search_keywords="한국어",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_special_characters_parentheses(self, empty_db_session, db_session_factory):
        """括号内文本应可搜索。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        count = await self._insert_and_search_keywords(
            empty_db_session, tag_cache,
            title="国模测试", first_message_excerpt="(国模测试过更新变量成功)",
            search_keywords="国模",
        )
        assert count == 1

    @pytest.mark.asyncio
    async def test_first_message_excerpt_indexed(self, empty_db_session, db_session_factory):
        """仅出现在 first_message_excerpt 中的词应被索引。

        FTS5 索引 title 和 first_message_excerpt 两列，因此只出现在 exceprt
        中的关键词也应命中午。
        """
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        # 关键词仅出现在 exceprt
        count = await self._insert_and_search_keywords(
            empty_db_session, tag_cache,
            title="标题不含此关键词", first_message_excerpt="但摘要包含独有词汇奥德赛论坛",
            search_keywords="奥德赛",
        )
        assert count >= 1, "FTS5 should index first_message_excerpt content"

    @pytest.mark.asyncio
    async def test_case_insensitive(self, empty_db_session, db_session_factory):
        """英文大小写不敏感匹配。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        count = await self._insert_and_search_keywords(
            empty_db_session, tag_cache,
            title="GEMINI", first_message_excerpt="",
            search_keywords="gemini",
        )
        assert count == 1


# ══════════════════════════════════════════════
# 2. FTS MATCH 正选关键词测试
# ══════════════════════════════════════════════


class TestFTSIncludeKeywords:
    """测试 FTS5 MATCH 正选关键词的各种查询模式。"""

    @pytest.mark.asyncio
    async def test_single_keyword_match(self, seeded_fts_varied_session, db_session_factory):
        """单个关键词：'汉化' 应仅命中包含该词的帖子。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_fts_varied_session, tag_cache, keywords="汉化",
        )
        titles = _titles(threads)
        assert "搬运工汉化教程" in titles
        # 不含"汉化"的帖子不应出现
        assert "纯爱小说分享" not in titles

    @pytest.mark.asyncio
    async def test_comma_separated_and_groups(self, seeded_fts_varied_session, db_session_factory):
        """逗号分隔多 AND 组：'搬运工,汉化' → 必须同时命中两组。

        但注意只有一条帖子同时包含两者。
        """
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_fts_varied_session, tag_cache, keywords="搬运工,汉化",
        )
        titles = _titles(threads)
        # 同时含'搬运工'和'汉化'的帖子
        assert "搬运工汉化教程" in titles

    @pytest.mark.asyncio
    async def test_slash_separated_or_keywords(self, seeded_fts_varied_session, db_session_factory):
        """斜杠分隔 OR 关键词：'汉化/纯爱' → 命中其一即可。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_fts_varied_session, tag_cache, keywords="汉化/纯爱",
        )
        titles = _titles(threads)
        assert "搬运工汉化教程" in titles
        assert "纯爱小说分享" in titles

    @pytest.mark.asyncio
    async def test_exact_match_quoted(self, empty_db_session, db_session_factory):
        """双引号精确匹配：'"keyword"' 语法跳过 jieba 分词，直接精确短语匹配。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        # 插入测试帖子（使用连续英文字母确保不被 jieba 拆散）
        t = Thread(
            thread_id=9010, channel_id=1, title="测试专用词Unique测试",
            first_message_excerpt="", author_id=1, created_at=datetime.now(),
        )
        empty_db_session.add(t)
        await empty_db_session.commit()

        # 用引号包围的精确匹配搜索
        threads, total = await _search(empty_db_session, tag_cache, keywords='"专用词Unique"')
        titles = _titles(threads)
        assert "测试专用词Unique测试" in titles

        await empty_db_session.delete(t)
        await empty_db_session.commit()

    @pytest.mark.asyncio
    async def test_mixed_and_or_groups(self, seeded_fts_varied_session, db_session_factory):
        """混合 AND+OR：'汉化/纯爱,Gemini' → (汉化 OR 纯爱) AND Gemini。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_fts_varied_session, tag_cache, keywords="汉化/纯爱,Gemini",
        )
        # 必须同时满足 (汉化 OR 纯爱) AND Gemini
        # 查看测试数据：只有 thread 201 有 Gemini 但也只有那是纯爱/汉化
        for t in threads:
            title_lower = t.title.lower()
            excerpt = (t.first_message_excerpt or "").lower()
            combined = title_lower + " " + excerpt
            has_gemini = "gemini" in combined
            has_hh_or_ca = "汉化" in combined or "纯爱" in combined
            assert has_gemini and has_hh_or_ca, (
                f"帖子 '{t.title}' 不满足 (汉化|纯爱) AND Gemini 条件"
            )

    @pytest.mark.asyncio
    async def test_no_keywords_returns_all(self, seeded_fts_varied_session, db_session_factory):
        """无关键词时不过滤 FTS，返回所有可见帖子。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_fts_varied_session, tag_cache, keywords=None,
        )
        # seeded_fts_varied 有 5 条帖子
        assert total == 5

    @pytest.mark.asyncio
    async def test_empty_keywords_returns_all(self, seeded_fts_varied_session, db_session_factory):
        """空字符串关键词也应视为无过滤。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_fts_varied_session, tag_cache, keywords="",
        )
        assert total == 5

    @pytest.mark.asyncio
    async def test_prefix_matching(self, empty_db_session, db_session_factory):
        """前缀匹配：FTS5 使用 '"token"*' 前缀语法。

        jieba 分词后的最后一个 token 会带 * 前缀匹配。
        搜索 '搬运' 应命中 '搬运工'。
        """
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        t = Thread(
            thread_id=9100, channel_id=1, title="搬运工汉化教程",
            first_message_excerpt="", author_id=1, created_at=datetime.now(),
        )
        empty_db_session.add(t)
        await empty_db_session.commit()

        threads, total = await _search(empty_db_session, tag_cache, keywords="搬运")
        assert total >= 1
        titles = _titles(threads)
        assert "搬运工汉化教程" in titles

        await empty_db_session.delete(t)
        await empty_db_session.commit()


# ══════════════════════════════════════════════
# 3. 排除关键词与豁免标记测试
# ══════════════════════════════════════════════


class TestFTSExcludeAndExemption:
    """测试排除关键词（exclude_keywords）和豁免标记（exemption_markers）的交互。"""

    # ── 排除关键词基本行为 ──

    @pytest.mark.asyncio
    async def test_exclude_single_keyword(self, seeded_basic_session, db_session_factory):
        """排除单个关键词 '百合破坏'，应移除所有含该词的帖子。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_basic_session, tag_cache,
            keywords=None,
            exclude_keywords="百合破坏",
            exclude_keyword_exemption_markers=[],
        )
        titles = _titles(threads)
        assert "关于百合破坏的讨论" not in titles
        assert "🈲百合破坏" not in titles
        assert "禁：请勿讨论百合破坏话题" not in titles
        assert "小说推荐" in titles
        assert "纯爱小说分享" in titles

    @pytest.mark.asyncio
    async def test_exclude_multiple_or(self, seeded_basic_session, db_session_factory):
        """多个排除关键词（空格分隔 = OR 逻辑）'百合破坏 小说' 应排除含任一关键词的帖子。

        当排除关键词覆盖全部 5 条帖子时结果应为 0。
        """
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        _, total = await _search(
            seeded_basic_session, tag_cache,
            keywords=None,
            exclude_keywords="百合破坏 小说",
            exclude_keyword_exemption_markers=[],
        )
        # '百合破坏' 命中 3 条（前三条），'小说' 命中 2 条（后两条）→ 全部排除
        assert total == 0

    @pytest.mark.asyncio
    async def test_exclude_with_include_keywords(self, seeded_basic_session, db_session_factory):
        """排除关键词 + 正选关键词组合：先正选后排除。

        '百合破坏' 为排除词，但豁免标记有 '禁'/'🈲'。
        无豁免标记的帖子 '关于百合破坏的讨论' 应被排除。
        """
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_basic_session, tag_cache,
            keywords=None,
            exclude_keywords="百合破坏",
            exclude_keyword_exemption_markers=["禁", "🈲"],
        )
        titles = _titles(threads)
        # 有豁免标记的帖子保留
        assert "🈲百合破坏" in titles
        assert "禁：请勿讨论百合破坏话题" in titles
        # 无豁免标记的帖子被排除
        assert "关于百合破坏的讨论" not in titles

    # ── 豁免标记 ──

    @pytest.mark.asyncio
    async def test_exemption_marker_proximity(self, seeded_basic_session, db_session_factory):
        """豁免标记：排除词附近有禁/🈲 时不排除。

        排除关键词 '百合破坏'，但标记 '禁'/'🈲' 在 NEAR(4) 范围内时豁免。
        因此 '🈲百合破坏' 和 '禁：请勿讨论百合破坏话题' 应保留。
        """
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_basic_session, tag_cache,
            keywords=None,
            exclude_keywords="百合破坏",
            exclude_keyword_exemption_markers=["禁", "🈲"],
        )
        titles = _titles(threads)
        # 豁免的应保留
        assert "🈲百合破坏" in titles
        assert "禁：请勿讨论百合破坏话题" in titles
        # 无标记的应排除
        assert "关于百合破坏的讨论" not in titles
        # 无关帖子应保留
        assert "小说推荐" in titles
        assert "纯爱小说分享" in titles

    @pytest.mark.asyncio
    async def test_exemption_prefix_keyword(self, seeded_basic_session, db_session_factory):
        """前缀排除 + 豁免：排除 '百合破'（前缀），豁免标记仍生效。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_basic_session, tag_cache,
            keywords=None,
            exclude_keywords="百合破",
            exclude_keyword_exemption_markers=["禁", "🈲"],
        )
        titles = _titles(threads)
        assert "🈲百合破坏" in titles
        assert "禁：请勿讨论百合破坏话题" in titles
        assert "关于百合破坏的讨论" not in titles

    @pytest.mark.asyncio
    async def test_exemption_no_markers(self, seeded_basic_session, db_session_factory):
        """无豁免标记时，所有含排除词的帖子都被排除。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_basic_session, tag_cache,
            keywords=None,
            exclude_keywords="百合破坏",
            exclude_keyword_exemption_markers=[],
        )
        titles = _titles(threads)
        assert "关于百合破坏的讨论" not in titles
        assert "🈲百合破坏" not in titles
        assert "禁：请勿讨论百合破坏话题" not in titles

    @pytest.mark.asyncio
    async def test_exemption_comma_separated_excludes(self, seeded_basic_session, db_session_factory):
        """逗号/顿号分隔的排除关键词：各排除词独立生效。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_basic_session, tag_cache,
            keywords=None,
            exclude_keywords="百合破坏 纯爱",
            exclude_keyword_exemption_markers=["禁", "🈲"],
        )
        # 排除"百合破坏"（有标记的豁免）和"纯爱"（无豁免）
        # '纯爱小说分享' 不含豁免标记 → 应被排除
        # '小说推荐' 不含任一排除词 → 保留
        titles = _titles(threads)
        # 豁免帖子
        assert "🈲百合破坏" in titles
        assert "禁：请勿讨论百合破坏话题" in titles
        assert "小说推荐" in titles
        # 无豁免的排除帖子
        assert "关于百合破坏的讨论" not in titles
        assert "纯爱小说分享" not in titles


# ══════════════════════════════════════════════
# 4. 搜索过滤器组合测试
# ══════════════════════════════════════════════


class TestSearchFilters:
    """测试各种非 FTS 过滤器与 FTS 关键词的组合行为。"""

    @pytest.mark.asyncio
    async def test_channel_filter(self, seeded_filter_session, db_session_factory):
        """频道过滤：仅返回指定频道的帖子。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache,
            keywords=None, channel_ids=[10],
        )
        titles = _titles(threads)
        assert "汉化RPG游戏推荐" in titles
        assert "原创汉化工具分享" in titles
        assert "百合RPG小说推荐" not in titles  # channel 20

    @pytest.mark.asyncio
    async def test_channel_exclusion(self, seeded_filter_session, db_session_factory):
        """频道排除：不返回排除频道的帖子。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache,
            keywords=None, exclude_channel_ids=[20],
        )
        titles = _titles(threads)
        assert "汉化RPG游戏推荐" in titles
        assert "百合RPG小说推荐" not in titles

    @pytest.mark.asyncio
    async def test_guild_filter(self, seeded_filter_session, db_session_factory):
        """服务器过滤：仅返回指定服务器的帖子（当未指定 channel_ids）。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache,
            keywords=None, guild_id=1,
        )
        titles = _titles(threads)
        # thread 401/402/403 的 guild_id=1 且 show_flag=True not_found=0
        # thread 404 的 guild_id=2 → 应排除
        assert "汉化RPG游戏推荐" in titles
        assert "纯爱原创故事" not in titles  # guild_id=2
        # thread 405/406 被隐藏
        assert "隐藏帖子不应出现" not in titles
        assert "未找到的帖子不应出现" not in titles

    @pytest.mark.asyncio
    async def test_author_filter(self, seeded_filter_session, db_session_factory):
        """作者过滤：仅返回指定作者的帖子。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache,
            keywords=None, include_authors=[1],
        )
        for t in threads:
            assert t.author_id == 1

    @pytest.mark.asyncio
    async def test_author_exclusion(self, seeded_filter_session, db_session_factory):
        """排除指定作者。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache,
            keywords=None, exclude_authors=[2],
        )
        for t in threads:
            assert t.author_id != 2

    @pytest.mark.asyncio
    async def test_author_name_search(self, seeded_filter_session, db_session_factory):
        """按作者名搜索（模糊匹配）。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache,
            keywords=None, author_name="author_one",
        )
        for t in threads:
            assert t.author_id == 1

    @pytest.mark.asyncio
    async def test_hidden_thread_excluded(self, seeded_filter_session, db_session_factory):
        """show_flag=False 的帖子不出现在搜索结果中。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache, keywords=None,
        )
        titles = _titles(threads)
        assert "隐藏帖子不应出现" not in titles

    @pytest.mark.asyncio
    async def test_not_found_thread_excluded(self, seeded_filter_session, db_session_factory):
        """not_found_count > 0 的帖子不出现在搜索结果中。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache, keywords=None,
        )
        titles = _titles(threads)
        assert "未找到的帖子不应出现" not in titles

    @pytest.mark.asyncio
    async def test_time_filter_created_after(self, seeded_filter_session, db_session_factory):
        """创建时间下限过滤。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache,
            keywords=None, created_after="2d",  # 2 天前至今
        )
        titles = _titles(threads)
        # thread 401 是 5 天前 → 应排除
        assert "汉化RPG游戏推荐" not in titles
        # thread 404 是 1 天前 → 应保留
        assert "纯爱原创故事" in titles

    @pytest.mark.asyncio
    async def test_reaction_count_range(self, seeded_filter_session, db_session_factory):
        """反应数范围过滤。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache,
            keywords=None, reaction_count_range="[10, 999999]",
        )
        for t in threads:
            assert t.reaction_count >= 10

    @pytest.mark.asyncio
    async def test_fts_plus_channel_filter(self, seeded_filter_session, db_session_factory):
        """FTS 关键词 + 频道过滤组合。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_filter_session, tag_cache,
            keywords="汉化", channel_ids=[10],
        )
        titles = _titles(threads)
        assert "汉化RPG游戏推荐" in titles
        assert "原创汉化工具分享" in titles
        # channel 20 的帖子不应出现
        for t in threads:
            assert t.channel_id == 10


# ══════════════════════════════════════════════
# 5. 排序算法测试
# ══════════════════════════════════════════════


class TestSearchSorting:
    """测试各排序算法的行为正确性。"""

    @pytest.mark.asyncio
    async def test_sort_by_reaction_count_desc(self, seeded_sorting_session, db_session_factory):
        """按反应数降序排序。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_sorting_session, tag_cache,
            keywords=None, sort_method="reaction_count", sort_order="desc",
        )
        for i in range(len(threads) - 1):
            assert threads[i].reaction_count >= threads[i + 1].reaction_count

    @pytest.mark.asyncio
    async def test_sort_by_reaction_count_asc(self, seeded_sorting_session, db_session_factory):
        """按反应数升序排序。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_sorting_session, tag_cache,
            keywords=None, sort_method="reaction_count", sort_order="asc",
        )
        for i in range(len(threads) - 1):
            assert threads[i].reaction_count <= threads[i + 1].reaction_count

    @pytest.mark.asyncio
    async def test_sort_by_created_at_desc(self, seeded_sorting_session, db_session_factory):
        """按创建时间降序（最新在前）。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_sorting_session, tag_cache,
            keywords=None, sort_method="created_at", sort_order="desc",
        )
        for i in range(len(threads) - 1):
            assert threads[i].created_at >= threads[i + 1].created_at

    @pytest.mark.asyncio
    async def test_sort_by_last_active_at_desc(self, seeded_sorting_session, db_session_factory):
        """按最后活跃时间降序排序。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_sorting_session, tag_cache,
            keywords=None, sort_method="last_active_at", sort_order="desc",
        )
        for i in range(len(threads) - 1):
            a = threads[i].last_active_at
            b = threads[i + 1].last_active_at
            if a is not None and b is not None:
                assert a >= b

    @pytest.mark.asyncio
    async def test_ucb1_comprehensive_sort(self, seeded_sorting_session, db_session_factory):
        """UCB1 综合排序返回结果（不验证具体分数，只验证有结果且不崩溃）。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_sorting_session, tag_cache,
            keywords=None, sort_method="comprehensive", sort_order="desc",
        )
        assert total == 5
        assert len(threads) == 5

    @pytest.mark.asyncio
    async def test_reddit_hot_sort(self, seeded_sorting_session, db_session_factory):
        """Reddit Hot 排序返回结果。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_sorting_session, tag_cache,
            keywords=None, sort_method="reddit_hot", sort_order="desc",
        )
        assert total == 5
        assert len(threads) == 5

    @pytest.mark.asyncio
    async def test_sort_by_reply_count(self, seeded_sorting_session, db_session_factory):
        """按回复数排序。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_sorting_session, tag_cache,
            keywords=None, sort_method="reply_count", sort_order="desc",
        )
        for i in range(len(threads) - 1):
            assert threads[i].reply_count >= threads[i + 1].reply_count

    @pytest.mark.asyncio
    async def test_sort_by_collection_count(self, seeded_sorting_session, db_session_factory):
        """按收藏数排序。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_sorting_session, tag_cache,
            keywords=None, sort_method="collection_count", sort_order="desc",
        )
        for i in range(len(threads) - 1):
            assert threads[i].collection_count >= threads[i + 1].collection_count

    @pytest.mark.asyncio
    async def test_custom_sort_fallback(self, seeded_sorting_session, db_session_factory):
        """自定义排序（custom_base_sort 回退到综合排序）。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        threads, total = await _search(
            seeded_sorting_session, tag_cache,
            keywords=None, sort_method="custom", custom_base_sort="reaction_count",
            sort_order="desc",
        )
        assert total == 5
        assert len(threads) == 5


# ══════════════════════════════════════════════
# 6. 边界值与边缘情况测试
# ══════════════════════════════════════════════


class TestSearchBoundaries:
    """测试边界值和边缘情况。"""

    @pytest.mark.asyncio
    async def test_no_results_fts_no_match(self, seeded_basic_session, db_session_factory):
        """FTS 关键词无匹配时返回空结果。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        _, total = await _search(
            seeded_basic_session, tag_cache,
            keywords="不存在的词汇xyz123",
        )
        assert total == 0

    @pytest.mark.asyncio
    async def test_fts_match_but_filter_excludes_all(self, seeded_filter_session, db_session_factory):
        """FTS 匹配到帖子但过滤器排除全部 → 返回空。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        # '汉化' 命中 thread 401/402，但 channel_ids=[20] 排除它们
        _, total = await _search(
            seeded_filter_session, tag_cache,
            keywords="汉化", channel_ids=[20],
        )
        assert total == 0

    @pytest.mark.asyncio
    async def test_pagination_offset(self, seeded_fts_varied_session, db_session_factory):
        """分页偏移：offset 跳过前 N 条结果。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        # 先取全部
        all_threads, all_total = await _search(
            seeded_fts_varied_session, tag_cache,
            keywords=None,
        )
        assert all_total == 5

        # 取 offset=2 limit=2
        query = ThreadSearchQuery()
        service = SearchService(
            session=seeded_fts_varied_session, tag_cache_service=tag_cache,
        )
        paged, paged_total = await service.search_threads_with_count(
            query=query, limit=2, offset=2,
            total_display_count=1000, exploration_factor=1.414, strength_weight=10.0,
        )
        assert paged_total == 5  # total 不变
        assert len(paged) == 2  # 仅 2 条

    @pytest.mark.asyncio
    async def test_limit_zero(self, seeded_fts_varied_session, db_session_factory):
        """limit=0 时返回空列表但 total 正确。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        query = ThreadSearchQuery()
        service = SearchService(
            session=seeded_fts_varied_session, tag_cache_service=tag_cache,
        )
        threads, total = await service.search_threads_with_count(
            query=query, limit=0, offset=0,
            total_display_count=1000, exploration_factor=1.414, strength_weight=10.0,
        )
        assert len(threads) == 0
        assert total == 5

    @pytest.mark.asyncio
    async def test_exclude_all_threads(self, seeded_basic_session, db_session_factory):
        """排除关键词命中全部帖子 → 返回空。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        # 所有帖子标题都含中文常见词 → 用一个应命中全部的排除词
        # 实际上在 seeded_basic 中不可能一个词命中全部，这里用组合
        _, total = await _search(
            seeded_basic_session, tag_cache,
            keywords=None,
            exclude_keywords="百合破坏 小说",
            exclude_keyword_exemption_markers=[],
        )
        # '小说' 命中 '小说推荐' 和 '纯爱小说分享'
        # '百合破坏' 命中前三条
        # 全部 5 条都被覆盖
        assert total == 0

    @pytest.mark.asyncio
    async def test_fts_update_triggers(self, empty_db_session, db_session_factory):
        """FTS5 UPDATE 触发器：更新 title 后 FTS 索引同步更新。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()

        t = Thread(
            thread_id=9200, channel_id=1, title="旧标题不含关键词",
            first_message_excerpt="", author_id=1, created_at=datetime.now(),
        )
        empty_db_session.add(t)
        await empty_db_session.commit()

        # 确保搜索不到
        _, total = await _search(empty_db_session, tag_cache, keywords="新关键词")
        assert total == 0

        # 更新标题
        t.title = "新关键词出现在标题中"
        empty_db_session.add(t)
        await empty_db_session.commit()

        # 现在应能搜到
        _, total = await _search(empty_db_session, tag_cache, keywords="新关键词")
        assert total == 1

        await empty_db_session.delete(t)
        await empty_db_session.commit()

    @pytest.mark.asyncio
    async def test_fts_delete_triggers(self, empty_db_session, db_session_factory):
        """FTS5 DELETE 触发器：删除帖子后 FTS 索引同步清除。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()

        t = Thread(
            thread_id=9300, channel_id=1, title="待删除帖子关键词测试",
            first_message_excerpt="", author_id=1, created_at=datetime.now(),
        )
        empty_db_session.add(t)
        await empty_db_session.commit()

        _, total = await _search(empty_db_session, tag_cache, keywords="待删除")
        assert total == 1

        await empty_db_session.delete(t)
        await empty_db_session.commit()

        _, total = await _search(empty_db_session, tag_cache, keywords="待删除")
        assert total == 0


# ══════════════════════════════════════════════
# 7. 参数化排除豁免测试（继承原有测试覆盖）
# ══════════════════════════════════════════════


class TestExclusionParameterized:
    """参数化的排除+豁免场景测试（覆盖原有 test_search_repository.py 的用例）。"""

    @pytest.mark.parametrize(
        "exclude_keywords, exemption_markers, expected_count, expected_present, expected_absent",
        [
            (
                "百合破坏",
                ["禁", "🈲"],
                4,
                {"🈲百合破坏", "禁：请勿讨论百合破坏话题", "小说推荐", "纯爱小说分享"},
                {"关于百合破坏的讨论"},
            ),
            (
                "百合破",
                ["禁", "🈲"],
                4,
                {"🈲百合破坏", "禁：请勿讨论百合破坏话题", "小说推荐", "纯爱小说分享"},
                {"关于百合破坏的讨论"},
            ),
            (
                "百合",
                ["禁", "🈲"],
                4,
                {"🈲百合破坏", "禁：请勿讨论百合破坏话题", "小说推荐", "纯爱小说分享"},
                {"关于百合破坏的讨论"},
            ),
            (
                "百合破坏",
                [],
                2,
                {"小说推荐", "纯爱小说分享"},
                {"关于百合破坏的讨论", "🈲百合破坏", "禁：请勿讨论百合破坏话题"},
            ),
            (
                "百合破",
                [],
                2,
                {"小说推荐", "纯爱小说分享"},
                {"关于百合破坏的讨论", "🈲百合破坏", "禁：请勿讨论百合破坏话题"},
            ),
            (
                "百合破坏 纯爱",
                ["禁", "🈲"],
                3,
                {"🈲百合破坏", "禁：请勿讨论百合破坏话题", "小说推荐"},
                {"关于百合破坏的讨论", "纯爱小说分享"},
            ),
        ],
    )
    @pytest.mark.asyncio
    async def test_exclusion_scenarios(
        self,
        seeded_basic_session: AsyncSession,
        db_session_factory: async_sessionmaker[AsyncSession],
        exclude_keywords: str,
        exemption_markers: List[str],
        expected_count: int,
        expected_present: Set[str],
        expected_absent: Set[str],
    ):
        """对反选关键词的各种场景进行参数化测试。"""
        tag_cache = _make_tag_cache(db_session_factory)
        await tag_cache.build_cache()
        service = SearchService(
            session=seeded_basic_session, tag_cache_service=tag_cache,
        )
        query = ThreadSearchQuery(
            exclude_keywords=exclude_keywords,
            exclude_keyword_exemption_markers=exemption_markers,
        )
        threads, total = await service.search_threads_with_count(
            query=query, offset=0, limit=10,
            total_display_count=1000, exploration_factor=1.414, strength_weight=10.0,
        )
        returned_titles = {t.title for t in threads}

        assert total == expected_count, (
            f"总数不匹配: exclude='{exclude_keywords}', markers={exemption_markers}"
        )
        assert returned_titles.issuperset(expected_present), (
            f"缺失预期结果: expected_present={expected_present}, got={returned_titles}"
        )
        assert not returned_titles.intersection(expected_absent), (
            f"出现不应出现的结果: expected_absent={expected_absent}, got={returned_titles}"
        )
