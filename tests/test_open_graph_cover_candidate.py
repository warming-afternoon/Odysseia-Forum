from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.booklist_item_repository import BooklistItemRepository
from models import Booklist, BooklistItem, Thread
from open_graph.booklist_open_graph_service import BooklistOpenGraphService


async def _candidate_rows():
    """模拟已按书单默认顺序返回的轻量候选行。"""
    yield 11, []
    yield 12, ["invalid-url"]
    yield 13, ["https://example.com/cover.jpg?version=2"]


@pytest.mark.asyncio
async def test_candidate_scan_skips_no_image_and_invalid_rows():
    """候选扫描跳过无图帖子，并选择后续首个合法图片。"""
    session = MagicMock()
    session.stream = AsyncMock(return_value=_candidate_rows())
    repository = BooklistItemRepository(session)

    candidate = await repository.get_open_graph_cover_candidate(
        booklist_id=1,
        default_sort_method="join_time",
        default_sort_order="desc",
    )

    assert candidate is not None
    assert candidate.thread_id == 13
    assert candidate.image_url == "https://example.com/cover.jpg?version=2"


@pytest.mark.asyncio
async def test_real_database_candidate_respects_sort_and_visibility(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    """真实 PostgreSQL 查询按默认排序跳过隐藏、失效及无图帖子。"""
    async with db_session_factory() as session:
        booklist = Booklist(
            owner_id=1,
            title="候选排序测试",
            default_sort_method="display_order",
            default_sort_order="desc",
        )
        session.add(booklist)
        await session.flush()
        assert booklist.id is not None

        threads = [
            Thread(
                channel_id=1,
                thread_id=101,
                title="隐藏图片",
                author_id=1,
                show_flag=False,
                thumbnail_urls=["https://example.com/hidden.jpg"],
            ),
            Thread(
                channel_id=1,
                thread_id=102,
                title="失效图片",
                author_id=1,
                not_found_count=1,
                thumbnail_urls=["https://example.com/missing.jpg"],
            ),
            Thread(
                channel_id=1,
                thread_id=103,
                title="无图",
                author_id=1,
                thumbnail_urls=[],
            ),
            Thread(
                channel_id=1,
                thread_id=104,
                title="有效图片",
                author_id=1,
                thumbnail_urls=["https://example.com/selected.jpg"],
            ),
        ]
        session.add_all(threads)
        session.add_all(
            [
                BooklistItem(
                    booklist_id=booklist.id,
                    thread_id=thread.thread_id,
                    display_order=40 - index * 10,
                )
                for index, thread in enumerate(threads)
            ]
        )
        await session.commit()

        repository = BooklistItemRepository(session)
        candidate = await repository.get_open_graph_cover_candidate(
            booklist_id=booklist.id,
            default_sort_method="display_order",
            default_sort_order="desc",
        )

    assert candidate is not None
    assert candidate.thread_id == 104
    assert candidate.image_url == "https://example.com/selected.jpg"


@pytest.mark.asyncio
async def test_real_database_private_booklist_is_hidden(
    db_session_factory: async_sessionmaker[AsyncSession],
):
    """真实数据库中的私有书单与不存在书单都不返回分享元数据。"""
    async with db_session_factory() as session:
        public_booklist = Booklist(
            owner_id=1,
            title="公开书单",
            is_public=True,
            cover_image_url="https://example.com/public-cover",
        )
        private_booklist = Booklist(
            owner_id=1,
            title="私有书单",
            is_public=False,
            cover_image_url="https://example.com/private-cover",
        )
        session.add_all([public_booklist, private_booklist])
        await session.commit()
        await session.refresh(public_booklist)
        await session.refresh(private_booklist)

    service = BooklistOpenGraphService(db_session_factory, MagicMock())
    public_result = await service.get_share_metadata(public_booklist.id)  # type: ignore[arg-type]
    private_result = await service.get_share_metadata(private_booklist.id)  # type: ignore[arg-type]
    missing_result = await service.get_share_metadata(999999)

    assert public_result is not None
    assert public_result.title == "公开书单"
    assert private_result is None
    assert missing_result is None
