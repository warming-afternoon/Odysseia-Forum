import time
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from dto.open_graph import BooklistCoverCandidateDTO, BooklistShareMetadataDTO
from open_graph.booklist_open_graph_service import BooklistOpenGraphService


def _metadata(image_url: str | None = None) -> BooklistShareMetadataDTO:
    """构造书单分享元数据。"""
    return BooklistShareMetadataDTO(
        title="测试书单",
        description=None,
        image_url=image_url,
        item_count=12,
        updated_at=datetime(2026, 8, 4),
    )


def _discord_url(expiry_epoch: int) -> str:
    """构造 Discord 签名附件 URL。"""
    return (
        "https://cdn.discordapp.com/attachments/123/456/cover.png"
        f"?ex={expiry_epoch:x}&is=abc&hm=signature"
    )


def _service(queue: MagicMock) -> BooklistOpenGraphService:
    """构造不访问数据库的 OG 服务。"""
    return BooklistOpenGraphService(MagicMock(), queue, 1, 2)


@pytest.mark.asyncio
async def test_url_with_more_than_one_hour_does_not_touch_queue():
    """有效期超过一小时直接返回且不访问 Redis 队列。"""
    url = _discord_url(int(time.time()) + 7200)
    queue = MagicMock()
    queue.get_or_enqueue = AsyncMock()
    service = _service(queue)
    service._load_metadata_and_candidate = AsyncMock(
        return_value=(
            _metadata(url),
            BooklistCoverCandidateDTO(thread_id=42, image_url=url),
            False,
        )
    )

    result = await service.get_share_metadata(1)

    assert result is not None and result.image_url == url
    queue.get_or_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_custom_cover_and_no_image_booklist_do_not_enqueue():
    """自定义封面直出，无图书单返回空图且都不触发重索引。"""
    queue = MagicMock()
    queue.get_or_enqueue = AsyncMock()
    custom_service = _service(queue)
    custom_service._load_metadata_and_candidate = AsyncMock(
        return_value=(_metadata("https://example.com/custom"), None, True)
    )
    empty_service = _service(queue)
    empty_service._load_metadata_and_candidate = AsyncMock(
        return_value=(_metadata(), None, False)
    )

    custom_result = await custom_service.get_share_metadata(1)
    empty_result = await empty_service.get_share_metadata(2)

    assert custom_result is not None
    assert custom_result.image_url == "https://example.com/custom"
    assert empty_result is not None and empty_result.image_url is None
    queue.get_or_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_discord_candidate_returns_directly_without_queue():
    """非 Discord 图床不检查签名也不访问重索引队列。"""
    url = "https://images.example.com/cover.png?version=1"
    queue = MagicMock()
    queue.get_or_enqueue = AsyncMock()
    service = _service(queue)
    service._load_metadata_and_candidate = AsyncMock(
        return_value=(
            _metadata(url),
            BooklistCoverCandidateDTO(thread_id=42, image_url=url),
            False,
        )
    )

    result = await service.get_share_metadata(1)

    assert result is not None and result.image_url == url
    queue.get_or_enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_invalid_ex_uses_synchronous_wait_branch():
    """无法解析 ex 的 Discord URL 按过期分支等待 Bot 结果。"""
    invalid_url = (
        "https://cdn.discordapp.com/attachments/123/456/cover.png"
        "?ex=not-hex&is=abc&hm=signature"
    )
    queue = MagicMock()
    queue.get_or_enqueue = AsyncMock(return_value="job")
    queue.wait_for_result = AsyncMock(return_value={"status": "failed"})
    service = _service(queue)
    service._load_metadata_and_candidate = AsyncMock(
        return_value=(
            _metadata(invalid_url),
            BooklistCoverCandidateDTO(thread_id=42, image_url=invalid_url),
            False,
        )
    )

    result = await service.get_share_metadata(1)

    assert result is not None and result.image_url is None
    queue.wait_for_result.assert_awaited_once_with("job", 2)


@pytest.mark.asyncio
async def test_last_hour_returns_old_url_and_enqueues_without_waiting():
    """最后一小时返回旧 URL 并只触发异步重索引。"""
    url = _discord_url(int(time.time()) + 1800)
    queue = MagicMock()
    queue.get_or_enqueue = AsyncMock(return_value="job")
    queue.wait_for_result = AsyncMock()
    service = _service(queue)
    service._load_metadata_and_candidate = AsyncMock(
        return_value=(
            _metadata(url),
            BooklistCoverCandidateDTO(thread_id=42, image_url=url),
            False,
        )
    )

    result = await service.get_share_metadata(1)

    assert result is not None and result.image_url == url
    queue.get_or_enqueue.assert_awaited_once_with(42)
    queue.wait_for_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_url_waits_then_requeries_and_validates_new_database_url():
    """过期 URL 成功同步后重查数据库，并返回新的有效 URL。"""
    stale_url = _discord_url(int(time.time()) - 60)
    fresh_url = _discord_url(int(time.time()) + 7200)
    queue = MagicMock()
    queue.get_or_enqueue = AsyncMock(return_value="job")
    queue.wait_for_result = AsyncMock(return_value={"status": "success"})
    service = _service(queue)
    service._load_metadata_and_candidate = AsyncMock(
        side_effect=[
            (
                _metadata(stale_url),
                BooklistCoverCandidateDTO(thread_id=42, image_url=stale_url),
                False,
            ),
            (
                _metadata(fresh_url),
                BooklistCoverCandidateDTO(thread_id=42, image_url=fresh_url),
                False,
            ),
        ]
    )

    result = await service.get_share_metadata(1)

    assert result is not None and result.image_url == fresh_url
    queue.wait_for_result.assert_awaited_once_with("job", 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_result", [None, {"status": "failed"}])
async def test_timeout_or_failure_returns_null_image(wait_result):
    """等待超时或 Bot 失败时返回空图片。"""
    stale_url = _discord_url(int(time.time()) - 60)
    queue = MagicMock()
    queue.get_or_enqueue = AsyncMock(return_value="job")
    queue.wait_for_result = AsyncMock(return_value=wait_result)
    service = _service(queue)
    service._load_metadata_and_candidate = AsyncMock(
        return_value=(
            _metadata(stale_url),
            BooklistCoverCandidateDTO(thread_id=42, image_url=stale_url),
            False,
        )
    )

    result = await service.get_share_metadata(1)

    assert result is not None and result.image_url is None
