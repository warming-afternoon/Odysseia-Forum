"""测试 Banner 系统的 TextChannel 支持。

覆盖：Channel 模型、parse_thread_link、BannerApplicationRequest 兼容、
BannerService 自动检测 target_type。"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlmodel import SQLModel

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from api.v1.schemas.banner import BannerApplicationRequest
from banner.banner_service import BannerService
from banner.dto.application_result import ApplicationResult
from models import BannerApplication, BannerCarousel, BannerWaitlist, Channel
from models.channel import Channel as ChannelModel
from shared.enum import ApplicationStatus, TargetType


class TestTargetType:
    """TargetType 枚举测试。"""

    def test_thread_value(self):
        assert TargetType.THREAD.value == 1

    def test_channel_value(self):
        assert TargetType.CHANNEL.value == 2

    def test_int_convertible(self):
        """IntEnum 可与 int 直接比较。"""
        assert TargetType.THREAD == 1
        assert TargetType.CHANNEL == 2


class TestChannelModel:
    """Channel 模型测试。"""

    @pytest.mark.asyncio
    async def test_create_channel(self, db_session_factory):
        """创建 Channel 记录并查询。"""
        async with db_session_factory() as session:
            channel = ChannelModel(
                channel_id=1374474903981527082,
                guild_id=1134557553011998840,
                name="赛事频道A",
                topic="某赛事讨论频道",
                category_id=1234567890123456789,
            )
            session.add(channel)
            await session.commit()
            await session.refresh(channel)

            assert channel.id is not None
            assert channel.name == "赛事频道A"
            assert channel.topic == "某赛事讨论频道"
            assert channel.category_id == 1234567890123456789
            assert channel.created_at is not None

    @pytest.mark.asyncio
    async def test_channel_unique_constraint(self, db_session_factory):
        """channel_id 唯一约束：重复插入应失败。"""
        async with db_session_factory() as session:
            c1 = ChannelModel(
                channel_id=1234567890123456789,
                guild_id=1,
                name="dup",
            )
            session.add(c1)
            await session.commit()

            c2 = ChannelModel(
                channel_id=1234567890123456789,
                guild_id=2,
                name="dup2",
            )
            session.add(c2)
            with pytest.raises(Exception):
                await session.commit()

    @pytest.mark.asyncio
    async def test_channel_created_at_auto_set(self, db_session_factory):
        """created_at 自动填充。"""
        async with db_session_factory() as session:
            channel = ChannelModel(
                channel_id=1,
                guild_id=1,
                name="test",
            )
            session.add(channel)
            await session.commit()
            await session.refresh(channel)
            assert channel.created_at is not None
            assert isinstance(channel.created_at, datetime)


class TestParseThreadLink:
    """parse_thread_link 解析测试。"""

    @pytest.fixture(autouse=True)
    def setup_main_guild_id(self):
        """注入 main_guild_id 到 banner 模块。"""
        import api.v1.routers.banner as banner_mod
        self._old_main_guild_id = banner_mod.main_guild_id
        banner_mod.main_guild_id = 1134557553011998840
        yield
        banner_mod.main_guild_id = self._old_main_guild_id

    def _parse(self, link: str):
        from api.v1.routers.banner import parse_thread_link
        return parse_thread_link(link)

    def test_full_url(self):
        """完整 Discord URL 解析。"""
        result = self._parse(
            "https://discord.com/channels/1134557553011998840/1234567890123456789"
        )
        assert result == (1134557553011998840, 1234567890123456789)

    def test_full_url_with_trailing_slash(self):
        result = self._parse(
            "https://discord.com/channels/1111111111111111111/2222222222222222222/"
        )
        assert result == (1111111111111111111, 2222222222222222222)

    def test_canary_url(self):
        """canary.discord.com 域名。"""
        result = self._parse(
            "https://canary.discord.com/channels/1134557553011998840/1374474903981527082"
        )
        assert result == (1134557553011998840, 1374474903981527082)

    def test_plain_numeric_id(self):
        """纯数字 ID → 使用 main_guild_id 拼接。"""
        result = self._parse("1234567890123456789")
        assert result == (1134557553011998840, 1234567890123456789)

    def test_invalid_url(self):
        """无效 URL 返回 None。"""
        assert self._parse("https://example.com/foo") is None

    def test_invalid_format(self):
        """非数字非 URL 返回 None。"""
        assert self._parse("abc123") is None

    def test_no_main_guild_id(self):
        """未配置 main_guild_id 时纯数字 ID 返回 None。"""
        import api.v1.routers.banner as banner_mod
        banner_mod.main_guild_id = 0
        try:
            assert self._parse("1234567890123456789") is None
        finally:
            banner_mod.main_guild_id = 1134557553011998840


class TestBannerApplicationRequest:
    """BannerApplicationRequest Pydantic 模型测试。"""

    def test_valid_request_with_url(self):
        """使用 Discord URL 提交。"""

        req = BannerApplicationRequest(
            thread_link="https://discord.com/channels/1/2",
            cover_image_url="https://example.com/img.png",
            target_scope="global",
        )
        assert req.thread_link == "https://discord.com/channels/1/2"

    def test_valid_request_with_plain_id(self):
        """使用纯数字 ID 提交。"""

        req = BannerApplicationRequest(
            thread_link="1234567890123456789",
            cover_image_url="https://example.com/img.png",
            target_scope="global",
        )
        assert req.thread_link == "1234567890123456789"

    def test_backward_compat_thread_id(self):
        """旧接口：传 thread_id 自动转为 thread_link。"""

        req = BannerApplicationRequest(
            thread_id="1234567890123456789",  # type: ignore[call-arg]
            cover_image_url="https://example.com/img.png",
            target_scope="global",
        )
        assert req.thread_link == "1234567890123456789"
        assert not hasattr(req, "thread_id")

    def test_both_thread_id_and_thread_link(self):
        """同时传 thread_id 和 thread_link → thread_link 优先。"""

        req = BannerApplicationRequest(
            thread_link="1234567890123456789",
            thread_id="1111111111111111111",  # type: ignore[call-arg]
            cover_image_url="https://example.com/img.png",
            target_scope="global",
        )
        assert req.thread_link == "1234567890123456789"

    def test_thread_link_too_short(self):
        """thread_link 长度不足 17 应报错。"""

        with pytest.raises(ValidationError):
            BannerApplicationRequest(
                thread_link="12345",
                cover_image_url="https://example.com/img.png",
                target_scope="global",
            )


class TestBannerServiceTargetDetection:
    """BannerService 自动检测 target_type 测试。"""

    @pytest.mark.asyncio
    async def test_detect_forum_thread(self, db_session_factory):
        """Thread 表中找到 → target_type=THREAD，检查作者。"""
        # 需要一条 Thread 记录
        from models.thread import Thread
        async with db_session_factory() as session:
            thread = Thread(
                thread_id=1234567890123456789,
                guild_id=1134557553011998840,
                channel_id=1111111111111111111,
                title="测试帖子",
                author_id=999,
            )
            session.add(thread)
            await session.commit()

            service = BannerService(session)
            result = await service.validate_application_request(
                target_id=1234567890123456789,
                guild_id=1134557553011998840,
                applicant_id=999,
                cover_image_url="https://example.com/img.png",
            )
            assert result.success is True
            assert result.target_type == TargetType.THREAD.value
            assert result.target_name == "测试帖子"

    @pytest.mark.asyncio
    async def test_forum_thread_wrong_author(self, db_session_factory):
        """Thread 表中找到但作者不匹配 → 拒绝。"""
        from models.thread import Thread
        async with db_session_factory() as session:
            thread = Thread(
                thread_id=1234567890123456789,
                guild_id=1,
                channel_id=1,
                title="别人的帖子",
                author_id=888,
            )
            session.add(thread)
            await session.commit()

            service = BannerService(session)
            result = await service.validate_application_request(
                target_id=1234567890123456789,
                guild_id=1,
                applicant_id=999,  # 不匹配
                cover_image_url="https://example.com/img.png",
            )
            assert result.success is False
            assert "只能为自己的帖子" in result.message

    @pytest.mark.asyncio
    async def test_detect_channel(self, db_session_factory):
        """Thread 表未找到但 Channel 表中有 → target_type=CHANNEL。"""
        async with db_session_factory() as session:
            channel = ChannelModel(
                channel_id=1374474903981527082,
                guild_id=1134557553011998840,
                name="赛事频道",
            )
            session.add(channel)
            await session.commit()

            service = BannerService(session)
            result = await service.validate_application_request(
                target_id=1374474903981527082,
                guild_id=1134557553011998840,
                applicant_id=123,  # 频道不校验作者
                cover_image_url="https://example.com/img.png",
            )
            assert result.success is True
            assert result.target_type == TargetType.CHANNEL.value
            assert result.target_name == "赛事频道"

    @pytest.mark.asyncio
    async def test_not_found_no_sync(self, db_session_factory):
        """Thread 和 Channel 都查不到且无 channel_sync → 返回错误。"""
        async with db_session_factory() as session:
            service = BannerService(session)
            result = await service.validate_application_request(
                target_id=1234567890123456789,
                guild_id=1,
                applicant_id=1,
                cover_image_url="https://example.com/img.png",
            )
            assert result.success is False
            assert "未被索引" in result.message

    @pytest.mark.asyncio
    async def test_auto_sync_channel(self, db_session_factory):
        """channel_sync 可用时，按需索引频道。"""
        mock_sync = MagicMock()
        mock_channel = ChannelModel(
            channel_id=1374474903981527082,
            guild_id=1134557553011998840,
            name="Discord赛事频道",
        )
        mock_sync.fetch_and_index = AsyncMock(return_value=mock_channel)

        async with db_session_factory() as session:
            service = BannerService(session, channel_sync=mock_sync)
            result = await service.validate_application_request(
                target_id=1374474903981527082,
                guild_id=1134557553011998840,
                applicant_id=123,
                cover_image_url="https://example.com/img.png",
            )
            assert result.success is True
            assert result.target_type == TargetType.CHANNEL.value
            assert result.target_name == "Discord赛事频道"
            mock_sync.fetch_and_index.assert_called_once()

    @pytest.mark.asyncio
    async def test_auto_sync_failed(self, db_session_factory):
        """channel_sync 也失败 → 返回错误。"""
        mock_sync = MagicMock()
        mock_sync.fetch_and_index = AsyncMock(return_value=None)

        async with db_session_factory() as session:
            service = BannerService(session, channel_sync=mock_sync)
            result = await service.validate_application_request(
                target_id=1234567890123456789,
                guild_id=1,
                applicant_id=1,
                cover_image_url="https://example.com/img.png",
            )
            assert result.success is False


class TestBannerApplicationWithTargetType:
    """Banner 申请创建时 target_type 正确写入。"""

    @pytest.mark.asyncio
    async def test_create_application_with_target_type(self, db_session_factory):
        """申请记录包含 target_type。"""
        async with db_session_factory() as session:
            app = BannerApplication(
                thread_id=1234567890123456789,
                channel_id=1,
                applicant_id=1,
                cover_image_url="https://example.com/img.png",
                target_scope="global",
                target_type=TargetType.CHANNEL.value,
                status=ApplicationStatus.PENDING.value,
            )
            session.add(app)
            await session.commit()
            await session.refresh(app)

            assert app.target_type == TargetType.CHANNEL.value

    @pytest.mark.asyncio
    async def test_default_target_type_is_thread(self, db_session_factory):
        """不传 target_type 时默认为 THREAD (1)。"""
        async with db_session_factory() as session:
            app = BannerApplication(
                thread_id=1234567890123456789,
                channel_id=1,
                applicant_id=1,
                cover_image_url="https://example.com/img.png",
                target_scope="global",
                status=ApplicationStatus.PENDING.value,
            )
            session.add(app)
            await session.commit()
            await session.refresh(app)

            assert app.target_type == TargetType.THREAD.value


class TestBannerCarouselWithTargetType:
    """BannerCarousel target_type 测试。"""

    @pytest.mark.asyncio
    async def test_carousel_item_has_target_type(self, db_session_factory):
        """轮播项包含 target_type。"""
        async with db_session_factory() as session:
            item = BannerCarousel(
                thread_id=1374474903981527082,
                channel_id=None,
                cover_image_url="https://example.com/img.png",
                title="赛事频道",
                target_type=TargetType.CHANNEL.value,
                start_time=datetime.now(timezone.utc).replace(tzinfo=None),
                end_time=datetime.now(timezone.utc).replace(tzinfo=None),
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)

            assert item.target_type == TargetType.CHANNEL.value


class TestBannerWaitlistWithTargetType:
    """BannerWaitlist target_type 测试。"""

    @pytest.mark.asyncio
    async def test_waitlist_item_has_target_type(self, db_session_factory):
        """等待列表项包含 target_type。"""
        async with db_session_factory() as session:
            item = BannerWaitlist(
                thread_id=1374474903981527082,
                channel_id=None,
                cover_image_url="https://example.com/img.png",
                title="赛事频道",
                target_type=TargetType.CHANNEL.value,
            )
            session.add(item)
            await session.commit()
            await session.refresh(item)

            assert item.target_type == TargetType.CHANNEL.value


class TestApplicationResult:
    """ApplicationResult dataclass 测试。"""

    def test_defaults(self):
        result = ApplicationResult(success=True, message="ok")
        assert result.target_type == 1
        assert result.guild_id == 0
        assert result.target_name == ""

    def test_channel_result(self):
        result = ApplicationResult(
            success=True,
            message="验证通过",
            target_type=TargetType.CHANNEL.value,
            guild_id=1134557553011998840,
            target_name="赛事频道A",
        )
        assert result.target_type == 2
        assert result.guild_id == 1134557553011998840
        assert result.target_name == "赛事频道A"
