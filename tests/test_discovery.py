"""
发现页仓库集成测试（PostgreSQL 后端）。

覆盖 discovery.py API 背后的数据层：
- DiscoveryRepository.get_latest_threads
- ThreadRepository.get_random_threads

PG 关注点：func.random() 排序，多表 JOIN，selectinload/joinedload
"""

import pytest
import pytest_asyncio
from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from models import Thread, Author, Tag, ThreadTagLink
from discovery.discovery_repository import DiscoveryRepository
from core.thread_repository import ThreadRepository
from shared.time_utils import utc_now


@pytest_asyncio.fixture(scope="function")
async def discovery_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """提供已清理的独立会话。"""
    async with db_session_factory() as session:
        yield session


@pytest_asyncio.fixture(scope="function")
async def seeded_discovery_session(
    discovery_session: AsyncSession,
) -> AsyncSession:
    """预填充多样化的帖子数据，用于发现页测试。"""
    # 创建作者
    authors = [
        Author(
            id=1,
            name="Author1",
            display_name="ADisp1",
            avatar_url="https://example.com/a1.png",
        ),
        Author(
            id=2,
            name="Author2",
            display_name="ADisp2",
            avatar_url="https://example.com/a2.png",
        ),
        Author(
            id=3,
            name="Author3",
            display_name="ADisp3",
            avatar_url="https://example.com/a3.png",
        ),
    ]
    discovery_session.add_all(authors)

    # 创建标签
    tags = [Tag(id=1, name="百合"), Tag(id=2, name="纯爱"), Tag(id=3, name="后宫")]
    discovery_session.add_all(tags)

    # 创建帖子（不同频道、不同时间）
    threads = [
        Thread(
            channel_id=1,
            thread_id=101,
            title="Latest Thread",
            author_id=1,
            created_at=utc_now(),
            show_flag=True,
            not_found_count=0,
        ),
        Thread(
            channel_id=1,
            thread_id=102,
            title="Hidden Thread",
            author_id=2,
            created_at=utc_now(),
            show_flag=False,
            not_found_count=0,
        ),
        Thread(
            channel_id=1,
            thread_id=103,
            title="NotFound Thread",
            author_id=3,
            created_at=utc_now(),
            show_flag=True,
            not_found_count=5,
        ),
        Thread(
            channel_id=2,
            thread_id=201,
            title="Channel 2 Thread",
            author_id=1,
            created_at=utc_now(),
            show_flag=True,
            not_found_count=0,
        ),
        Thread(
            channel_id=1,
            thread_id=104,
            title="Reaction Thread",
            author_id=2,
            created_at=utc_now(),
            show_flag=True,
            not_found_count=0,
            reaction_count=50,
        ),
    ]
    discovery_session.add_all(threads)
    await discovery_session.commit()

    # 获取内部 ID → 直接创建 ThreadTagLink（ThreadTagLink.thread_id = Thread.id）
    for t in threads:
        await discovery_session.refresh(t)
    id_map = {t.thread_id: t.id for t in threads}

    links = [
        ThreadTagLink(thread_id=id_map[101], tag_id=1),
        ThreadTagLink(thread_id=id_map[101], tag_id=2),
        ThreadTagLink(thread_id=id_map[201], tag_id=3),
    ]
    discovery_session.add_all(links)
    await discovery_session.commit()
    return discovery_session


@pytest.mark.asyncio
class TestGetLatestThreads:
    """获取最新帖子 — 过滤 + 排序"""

    async def test_get_latest_threads_basic(
        self, seeded_discovery_session: AsyncSession
    ):
        """基本拉取：排除隐藏/软删除帖子"""
        repo = DiscoveryRepository(seeded_discovery_session)
        threads = await repo.get_latest_threads(limit=10, offset=0, prefs=None)
        # 应包含: 101(Latest), 201(Ch2), 104(Reaction)
        # 排除: 102(Hidden, show_flag=False), 103(NotFound, not_found_count=5)
        thread_ids = {t.thread_id for t in threads}
        assert 101 in thread_ids
        assert 201 in thread_ids
        assert 104 in thread_ids
        assert 102 not in thread_ids
        assert 103 not in thread_ids

    async def test_get_latest_threads_limit(
        self, seeded_discovery_session: AsyncSession
    ):
        """分页限制"""
        repo = DiscoveryRepository(seeded_discovery_session)
        threads = await repo.get_latest_threads(limit=2, offset=0, prefs=None)
        assert len(threads) == 2

    async def test_get_latest_threads_offset(
        self, seeded_discovery_session: AsyncSession
    ):
        """偏移分页"""
        repo = DiscoveryRepository(seeded_discovery_session)
        page1 = await repo.get_latest_threads(limit=2, offset=0, prefs=None)
        page2 = await repo.get_latest_threads(limit=2, offset=2, prefs=None)
        all_ids = {t.thread_id for t in page1} | {t.thread_id for t in page2}
        # 应看到所有 3 个可见帖子
        assert len(all_ids) == 3

    async def test_get_latest_threads_eager_loading(
        self, seeded_discovery_session: AsyncSession
    ):
        """预加载 author 关系"""
        repo = DiscoveryRepository(seeded_discovery_session)
        threads = await repo.get_latest_threads(limit=10, offset=0, prefs=None)

        # 验证 author 被预加载（不触发 lazy load）
        for t in threads:
            if t.thread_id == 101:
                assert t.author is not None
                assert t.author.name == "Author1"

    async def test_get_latest_with_preferences_channel(
        self, seeded_discovery_session: AsyncSession
    ):
        """偏好过滤 → preferred_channels"""
        from dto.preferences.user_search_preferences_dto import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            preferred_channels=[2],  # 只看 channel 2
        )
        repo = DiscoveryRepository(seeded_discovery_session)
        threads = await repo.get_latest_threads(limit=10, offset=0, prefs=prefs)
        thread_ids = {t.thread_id for t in threads}
        assert thread_ids == {201}  # 只有 channel 2 的帖子


@pytest.mark.asyncio
class TestGetRandomThreads:
    """随机帖子查询 — func.random()"""

    async def test_get_random_threads_basic(
        self, seeded_discovery_session: AsyncSession
    ):
        """基本随机查询"""
        repo = ThreadRepository(seeded_discovery_session)
        threads = await repo.get_random_threads(
            limit=2,
            channel_ids=[1],
        )
        assert len(threads) == 2
        # 应只从 channel 1 获取
        for t in threads:
            assert t.channel_id == 1
            assert t.show_flag is True
            assert t.not_found_count == 0

    async def test_get_random_threads_exclusion(
        self, seeded_discovery_session: AsyncSession
    ):
        """排除指定频道"""
        repo = ThreadRepository(seeded_discovery_session)
        threads = await repo.get_random_threads(
            limit=10,
            channel_ids=[1, 2],
            exclude_channel_ids=[1],
        )
        # 排除 channel 1 → 只有 channel 2 的帖子
        for t in threads:
            assert t.channel_id == 2

    async def test_get_random_threads_limit(
        self, seeded_discovery_session: AsyncSession
    ):
        """Limit 约束"""
        repo = ThreadRepository(seeded_discovery_session)
        threads = await repo.get_random_threads(
            limit=1,
            channel_ids=[1],
        )
        assert len(threads) == 1

    async def test_get_random_threads_empty_channel(
        self, seeded_discovery_session: AsyncSession
    ):
        """空频道列表 → 空结果"""
        repo = ThreadRepository(seeded_discovery_session)
        threads = await repo.get_random_threads(
            limit=10,
            channel_ids=None,
        )
        # None means all channels — should return all visible threads
        assert len(threads) >= 3
