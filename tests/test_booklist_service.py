import pytest
import pytest_asyncio
from typing import AsyncGenerator
from datetime import datetime
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession
from sqlmodel import delete

from models import Booklist
from models import BooklistItem
from models import Thread
from models import Author
from models import ThreadTagLink
from models import ThreadFollow
from core.booklist_repository import BooklistRepository
from core.booklist_item_repository import BooklistItemRepository
from shared.time_utils import utc_now
from api.v1.schemas.booklist.booklist_item_add_data import BooklistItemAddData


@pytest_asyncio.fixture(scope="function")
async def seeded_db_session(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncGenerator[AsyncSession, None]:
    """
    提供一个填充了测试数据的数据库会话。
    """
    async with db_session_factory() as session:
        # 创建作者
        author = Author(
            id=1,
            name="TestAuthor",
            global_name="GlobalTest",
            display_name="DisplayTest",
            avatar_url="https://example.com/avatar.png",
        )
        session.add(author)
        # 创建帖子
        threads = [
            Thread(
                channel_id=100,
                thread_id=1001,
                title="Test Thread 1",
                author_id=1,
                created_at=utc_now(),
                reaction_count=5,
                reply_count=2,
            ),
            Thread(
                channel_id=100,
                thread_id=1002,
                title="Test Thread 2",
                author_id=1,
                created_at=utc_now(),
                reaction_count=10,
                reply_count=3,
            ),
        ]
        session.add_all(threads)
        await session.commit()

        yield session

        # 在每个测试结束后清理数据
        await session.execute(delete(BooklistItem))
        await session.execute(delete(Booklist))
        await session.execute(delete(ThreadTagLink))
        await session.execute(delete(ThreadFollow))
        await session.execute(delete(Thread))
        await session.execute(delete(Author))
        await session.commit()


@pytest.mark.asyncio
async def test_create_booklist(seeded_db_session: AsyncSession):
    """测试创建书单"""
    service = BooklistRepository(seeded_db_session)
    booklist = await service.create_booklist(
        owner_id=123,
        title="My Booklist",
        description="A test booklist",
        cover_image_url="https://example.com/cover.jpg",
        is_public=True,
        display_type=1,
    )
    assert booklist.id is not None
    assert booklist.title == "My Booklist"
    assert booklist.owner_id == 123
    assert booklist.item_count == 0
    assert booklist.collection_count == 0
    assert booklist.view_count == 0


@pytest.mark.asyncio
async def test_get_booklist(seeded_db_session: AsyncSession):
    """测试获取书单"""
    service = BooklistRepository(seeded_db_session)
    created = await service.create_booklist(
        owner_id=456, title="Get Test", description="Test"
    )
    assert created.id is not None
    fetched = await service.get_booklist(created.id)
    assert fetched is not None
    assert fetched.id == created.id
    assert fetched.title == "Get Test"


@pytest.mark.asyncio
async def test_update_booklist(seeded_db_session: AsyncSession):
    """测试更新书单"""
    service = BooklistRepository(seeded_db_session)
    created = await service.create_booklist(
        owner_id=789, title="Original", description="Old"
    )
    assert created.id is not None
    updated = await service.update_booklist(
        booklist_id=created.id,
        title="Updated",
        description="New",
        is_public=False,
        display_type=2,
    )
    assert updated is not None
    assert updated.title == "Updated"
    assert updated.description == "New"
    assert updated.is_public is False
    assert updated.display_type == 2


@pytest.mark.asyncio
async def test_delete_booklist(seeded_db_session: AsyncSession):
    """测试删除书单"""
    service = BooklistRepository(seeded_db_session)
    created = await service.create_booklist(
        owner_id=999, title="To Delete", description="Will be deleted"
    )
    assert created.id is not None
    success = await service.delete_booklist(created.id)
    assert success is True
    fetched = await service.get_booklist(created.id)
    assert fetched is None


@pytest.mark.asyncio
async def test_add_thread_to_booklist(seeded_db_session: AsyncSession):
    """测试向书单添加帖子"""
    service = BooklistRepository(seeded_db_session)
    booklist = await service.create_booklist(owner_id=111, title="Add Test")
    assert booklist.id is not None
    booklist_id = booklist.id
    # 添加第一个帖子
    items = await service.add_threads_to_booklist(
        booklist_id=booklist_id,
        items=[BooklistItemAddData(thread_id=1001, comment="Great thread", display_order=1)],
    )
    assert len(items) == 1
    item1 = items[0]
    assert item1.id is not None
    assert item1.thread_id == 1001
    assert item1.comment == "Great thread"
    assert item1.display_order == 1
    # 书单的 item_count 应增加
    updated_booklist = await service.get_booklist(booklist_id)
    assert updated_booklist is not None
    assert updated_booklist.item_count == 1
    # 添加第二个帖子，不指定 display_order，应自动递增
    items2 = await service.add_threads_to_booklist(
        booklist_id=booklist_id,
        items=[BooklistItemAddData(thread_id=1002, comment="Another thread")],
    )
    assert items2[0].display_order == 2
    updated_booklist = await service.get_booklist(booklist_id)
    assert updated_booklist is not None
    assert updated_booklist.item_count == 2


@pytest.mark.asyncio
async def test_remove_thread_from_booklist(seeded_db_session: AsyncSession):
    """测试从书单移除帖子"""
    service = BooklistRepository(seeded_db_session)
    booklist = await service.create_booklist(owner_id=222, title="Remove Test")
    assert booklist.id is not None
    booklist_id = booklist.id
    await service.add_threads_to_booklist(
        booklist_id, items=[BooklistItemAddData(thread_id=1001)]
    )
    # 移除存在的帖子
    deleted = await service.remove_threads_from_booklist(booklist_id, [1001])
    assert deleted == 1
    updated_booklist = await service.get_booklist(booklist_id)
    assert updated_booklist is not None
    assert updated_booklist.item_count == 0
    # 移除不存在的帖子
    deleted = await service.remove_threads_from_booklist(booklist_id, [9999])
    assert deleted == 0


@pytest.mark.asyncio
async def test_list_booklists(seeded_db_session: AsyncSession):
    """测试列出书单"""
    service = BooklistRepository(seeded_db_session)
    # 创建多个书单
    for i in range(5):
        await service.create_booklist(
            owner_id=333,
            title=f"Booklist {i}",
            is_public=(i % 2 == 0),
        )
    # 列出所有书单
    booklists, total = await service.list_booklists(owner_id=333, limit=10, offset=0)
    assert total == 5
    assert len(booklists) == 5
    # 过滤公开书单
    public, total_public = await service.list_booklists(
        owner_id=333, is_public=True, limit=10, offset=0
    )
    assert total_public == 3  # 0,2,4 是公开的
    # 分页测试
    page1, total1 = await service.list_booklists(owner_id=333, limit=2, offset=0)
    assert len(page1) == 2
    assert total1 == 5


@pytest.mark.asyncio
async def test_get_booklist_items(seeded_db_session: AsyncSession):
    """测试获取书单内容"""
    service = BooklistRepository(seeded_db_session)
    booklist = await service.create_booklist(owner_id=444, title="Items Test")
    assert booklist.id is not None
    booklist_id = booklist.id
    await service.add_threads_to_booklist(
        booklist_id,
        items=[
            BooklistItemAddData(thread_id=1001, comment="First"),
            BooklistItemAddData(thread_id=1002, comment="Second"),
        ],
    )
    item_service = BooklistItemRepository(seeded_db_session)
    items, total = await item_service.get_booklist_items_with_details(
        booklist_id, display_type=1, limit=10, offset=0
    )
    assert total == 2
    assert len(items) == 2
    # 检查返回数据（BooklistItemDetail 是 Pydantic 模型，用属性访问）
    # display_type=1 按 created_at DESC 排序，后添加的 thread 1002 排前面
    first = items[0]
    assert first.thread_id == 1002
    assert first.comment == "Second"
    assert first.title is not None
    assert first.author is not None
    # 分页测试
    items_page1, total_page1 = await item_service.get_booklist_items_with_details(
        booklist_id, display_type=1, limit=1, offset=0
    )
    assert len(items_page1) == 1
    assert total_page1 == 2


@pytest.mark.asyncio
async def test_increment_view_count(seeded_db_session: AsyncSession):
    """测试增加查看次数"""
    service = BooklistRepository(seeded_db_session)
    booklist = await service.create_booklist(owner_id=555, title="View Test")
    assert booklist.id is not None
    booklist_id = booklist.id
    assert booklist.view_count == 0
    await service.increment_view_count(booklist_id)
    updated = await service.get_booklist(booklist_id)
    assert updated is not None
    assert updated.view_count == 1


@pytest.mark.asyncio
async def test_update_collection_count(seeded_db_session: AsyncSession):
    """测试更新收藏次数"""
    service = BooklistRepository(seeded_db_session)
    booklist = await service.create_booklist(owner_id=666, title="Collection Test")
    assert booklist.id is not None
    booklist_id = booklist.id
    assert booklist.collection_count == 0
    await service.update_collection_count(booklist_id, +1)
    updated = await service.get_booklist(booklist_id)
    assert updated is not None
    assert updated.collection_count == 1
    await service.update_collection_count(booklist_id, -1)
    updated = await service.get_booklist(booklist_id)
    assert updated is not None
    assert updated.collection_count == 0
    # 不应低于0
    await service.update_collection_count(booklist_id, -5)
    updated = await service.get_booklist(booklist_id)
    assert updated is not None
    assert updated.collection_count == 0
