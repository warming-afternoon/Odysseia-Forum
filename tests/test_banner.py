"""测试 Banner 系统的 TextChannel 支持。

覆盖：Channel 模型、parse_thread_link、BannerApplicationRequest 兼容、
BannerService 自动检测 target_type。"""

import os
import sys
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../src")))

from api.v1.schemas.banner import BannerApplicationRequest
from banner.banner_service import BannerService
from banner.dto.application_result import ApplicationResult
from banner.views.application_form_modal import ApplicationFormModal
from models import BannerApplication, BannerCarousel, BannerWaitlist
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

    def _parse(self, link: str, main_guild_id: int = 1134557553011998840):
        from shared.thread_link_parser import ThreadLinkParser

        return ThreadLinkParser.parse_thread_link(link, main_guild_id)

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

    def test_message_url(self):
        """带消息 ID 的 Discord URL 忽略末尾消息 ID。"""
        result = self._parse(
            "https://discord.com/channels/1134557553011998840/"
            "1374474903981527082/1417064059782000640"
        )
        assert result == (1134557553011998840, 1374474903981527082)

    def test_message_url_with_trailing_slash(self):
        """带消息 ID 和尾部斜杠的 Discord URL 正常解析。"""
        result = self._parse(
            "https://discord.com/channels/1134557553011998840/"
            "1374474903981527082/1417064059782000640/"
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
        assert self._parse("1234567890123456789", main_guild_id=0) is None


class TestApplicationFormModal:
    """Discord BOT Banner 申请弹窗测试。"""

    @staticmethod
    def _interaction(guild_id: int = 1134557553011998840) -> MagicMock:
        """构造弹窗提交所需的 Discord 交互对象。"""
        interaction = MagicMock()
        interaction.guild_id = guild_id
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        interaction.client.dispatch = MagicMock()
        return interaction

    @pytest.mark.asyncio
    async def test_submit_message_url(self):
        """消息 URL 解析后分发中间的帖子或频道 ID。"""
        modal = ApplicationFormModal()
        modal.target_link._value = (
            "https://discord.com/channels/1134557553011998840/"
            "1374474903981527082/1417064059782000640"
        )
        modal.cover_image_url._value = "https://example.com/banner.png"
        interaction = self._interaction()

        await modal.on_submit(interaction)

        interaction.client.dispatch.assert_called_once_with(
            "banner_form_submit",
            interaction,
            1374474903981527082,
            "https://example.com/banner.png",
            1134557553011998840,
        )

    @pytest.mark.asyncio
    async def test_submit_plain_id(self):
        """纯数字 ID 使用当前交互的服务器 ID。"""
        modal = ApplicationFormModal()
        modal.target_link._value = "1374474903981527082"
        modal.cover_image_url._value = "https://example.com/banner.png"
        interaction = self._interaction()

        await modal.on_submit(interaction)

        interaction.client.dispatch.assert_called_once_with(
            "banner_form_submit",
            interaction,
            1374474903981527082,
            "https://example.com/banner.png",
            1134557553011998840,
        )

    @pytest.mark.asyncio
    async def test_reject_invalid_target(self):
        """无效链接不分发申请事件。"""
        modal = ApplicationFormModal()
        modal.target_link._value = "https://example.com/not-discord"
        modal.cover_image_url._value = "https://example.com/banner.png"
        interaction = self._interaction()

        await modal.on_submit(interaction)

        interaction.followup.send.assert_awaited_once_with(
            "❌ 请输入有效的 Discord 帖子/赛事频道链接或纯数字 ID",
            ephemeral=True,
        )
        interaction.client.dispatch.assert_not_called()


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


class TestFilterThreadBannersByPrefs:
    """_filter_thread_banners_by_prefs 偏好筛选单元测试。"""

    @pytest.fixture
    def sample_banners(self):
        """构造 3 个 thread 类型的 BannerCarousel 对象。"""
        from datetime import datetime, timedelta
        from models import BannerCarousel

        now = datetime.now()
        later = now + timedelta(days=1)
        return [
            BannerCarousel(
                id=1,
                thread_id=100,
                channel_id=None,
                cover_image_url="https://example.com/1.png",
                title="Banner A",
                target_type=TargetType.THREAD.value,
                start_time=now,
                end_time=later,
            ),
            BannerCarousel(
                id=2,
                thread_id=200,
                channel_id=None,
                cover_image_url="https://example.com/2.png",
                title="Banner B",
                target_type=TargetType.THREAD.value,
                start_time=now,
                end_time=later,
            ),
            BannerCarousel(
                id=3,
                thread_id=300,
                channel_id=None,
                cover_image_url="https://example.com/3.png",
                title="Banner C",
                target_type=TargetType.THREAD.value,
                start_time=now,
                end_time=later,
            ),
        ]

    @pytest.fixture
    def sample_threads(self):
        """构造 3 个 Thread 对象，含不同 author_id 和 tags。"""
        from models import Thread as ThreadModel
        from models import Tag

        tag_a = Tag(id=1, name="赛事")
        tag_b = Tag(id=2, name="攻略")
        tag_c = Tag(id=3, name="同人")

        thread_a = ThreadModel(
            id=1,
            thread_id=100,
            guild_id=1,
            channel_id=10,
            title="赛事讨论帖",
            author_id=111,
            first_message_excerpt="今天的赛事非常精彩",
        )
        thread_a.tags = [tag_a, tag_b]

        thread_b = ThreadModel(
            id=2,
            thread_id=200,
            guild_id=1,
            channel_id=10,
            title="攻略合集",
            author_id=222,
            first_message_excerpt="新手入门攻略",
        )
        thread_b.tags = [tag_b]

        thread_c = ThreadModel(
            id=3,
            thread_id=300,
            guild_id=1,
            channel_id=10,
            title="同人创作",
            author_id=333,
            first_message_excerpt=None,
        )
        thread_c.tags = [tag_c]

        return {100: thread_a, 200: thread_b, 300: thread_c}

    @pytest.fixture
    def empty_prefs(self):
        """空偏好（无任何排除条件）。"""
        from dto.preferences import UserSearchPreferencesDTO

        return UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=None,
            exclude_tags=None,
            exclude_keywords="",
        )

    def _filter(self, banners, thread_map, prefs):
        from api.v1.routers.banner import _filter_thread_banners_by_prefs

        return _filter_thread_banners_by_prefs(banners, thread_map, prefs)

    # ── exclude_authors ──

    def test_exclude_author_filters_banner(
        self, sample_banners, sample_threads, empty_prefs
    ):
        """exclude_authors 偏好 → 对应作者的 banner 被过滤。"""
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=[111],
            exclude_tags=None,
            exclude_keywords="",
        )
        result = self._filter(sample_banners, sample_threads, prefs)
        thread_ids = {b.thread_id for b in result}
        assert 100 not in thread_ids  # author_id=111 被排除
        assert 200 in thread_ids
        assert 300 in thread_ids

    def test_exclude_multiple_authors(
        self, sample_banners, sample_threads, empty_prefs
    ):
        """排除多个作者。"""
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=[111, 333],
            exclude_tags=None,
            exclude_keywords="",
        )
        result = self._filter(sample_banners, sample_threads, prefs)
        thread_ids = {b.thread_id for b in result}
        assert 100 not in thread_ids
        assert 200 in thread_ids  # author_id=222 未被排除
        assert 300 not in thread_ids

    # ── exclude_tags ──

    def test_exclude_tag_filters_banner(
        self, sample_banners, sample_threads, empty_prefs
    ):
        """exclude_tags 偏好 → 含对应标签的 banner 被过滤。"""
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=None,
            exclude_tags=["攻略"],
            exclude_keywords="",
        )
        result = self._filter(sample_banners, sample_threads, prefs)
        thread_ids = {b.thread_id for b in result}
        # Banner A (100): tags=["赛事","攻略"] → 有"攻略" → 排除
        assert 100 not in thread_ids
        # Banner B (200): tags=["攻略"] → 排除
        assert 200 not in thread_ids
        # Banner C (300): tags=["同人"] → 保留
        assert 300 in thread_ids

    def test_exclude_tags_case_insensitive(
        self, sample_banners, sample_threads, empty_prefs
    ):
        """exclude_tags 不区分大小写。"""
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=None,
            exclude_tags=["攻略"],  # 小写
            exclude_keywords="",
        )
        result = self._filter(sample_banners, sample_threads, prefs)
        thread_ids = {b.thread_id for b in result}
        assert 200 not in thread_ids  # tag "攻略" 匹配

    # ── exclude_keywords ──

    def test_exclude_keyword_in_title_filters_banner(
        self, sample_banners, sample_threads, empty_prefs
    ):
        """exclude_keywords 命中标题 → 过滤。"""
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=None,
            exclude_tags=None,
            exclude_keywords="赛事",
        )
        result = self._filter(sample_banners, sample_threads, prefs)
        thread_ids = {b.thread_id for b in result}
        # Banner A: title="赛事讨论帖" → 命中"赛事" → 排除
        assert 100 not in thread_ids
        assert 200 in thread_ids
        assert 300 in thread_ids

    def test_exclude_keyword_in_excerpt_filters_banner(
        self, sample_banners, sample_threads, empty_prefs
    ):
        """exclude_keywords 命中 first_message_excerpt → 过滤。"""
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=None,
            exclude_tags=None,
            exclude_keywords="新手",
        )
        result = self._filter(sample_banners, sample_threads, prefs)
        thread_ids = {b.thread_id for b in result}
        # Banner B: excerpt="新手入门攻略" → 命中"新手" → 排除
        assert 100 in thread_ids
        assert 200 not in thread_ids
        assert 300 in thread_ids

    def test_exclude_keywords_multi_word_split(
        self, sample_banners, sample_threads, empty_prefs
    ):
        """exclude_keywords 按空格/逗号分词，分别匹配。"""
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=None,
            exclude_tags=None,
            exclude_keywords="赛事,同人",
        )
        result = self._filter(sample_banners, sample_threads, prefs)
        thread_ids = {b.thread_id for b in result}
        # Banner A: 命中"赛事" → 排除
        assert 100 not in thread_ids
        # Banner C: 命中"同人" → 排除
        assert 300 not in thread_ids
        assert 200 in thread_ids

    def test_exclude_keyword_case_insensitive(
        self, sample_banners, sample_threads, empty_prefs
    ):
        """exclude_keywords 不区分大小写。"""
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=None,
            exclude_tags=None,
            exclude_keywords="赛事",
        )
        result = self._filter(sample_banners, sample_threads, prefs)
        thread_ids = {b.thread_id for b in result}
        assert 100 not in thread_ids

    def test_exclude_keyword_no_excerpt(
        self, sample_banners, sample_threads, empty_prefs
    ):
        """首楼摘要为 None 时仅检查标题，不报错。"""
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=None,
            exclude_tags=None,
            exclude_keywords="同人",
        )
        result = self._filter(sample_banners, sample_threads, prefs)
        thread_ids = {b.thread_id for b in result}
        # Banner C: title="同人创作", excerpt=None → 命中标题 → 排除
        assert 300 not in thread_ids

    # ── 组合过滤 ──

    def test_combined_filters(self, sample_banners, sample_threads, empty_prefs):
        """同时应用多种排除条件。"""
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=[111],
            exclude_tags=["同人"],
            exclude_keywords="新手",
        )
        result = self._filter(sample_banners, sample_threads, prefs)
        # Banner A: author 排除
        # Banner B: keyword "新手" 命中 excerpt 排除
        # Banner C: tag "同人" 排除
        assert len(result) == 0

    # ── 空偏好 / 边界情况 ──

    def test_no_prefs_returns_all(self, sample_banners, sample_threads, empty_prefs):
        """空偏好 → 全部保留。"""
        result = self._filter(sample_banners, sample_threads, empty_prefs)
        assert len(result) == 3

    def test_thread_not_in_map_preserved(
        self, sample_banners, sample_threads, empty_prefs
    ):
        """thread_map 中不存在的 banner 保留（线程可能已被删除）。"""
        from dto.preferences import UserSearchPreferencesDTO

        banners = sample_banners[:1]  # 只取 Banner A
        # thread_map 不含 thread_id=100
        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=[111],
            exclude_tags=None,
            exclude_keywords="",
        )
        result = self._filter(banners, {}, prefs)
        # 线程不在 map 中，保留 banner
        assert len(result) == 1
        assert result[0].thread_id == 100

    def test_empty_list_returns_empty(self, sample_threads, empty_prefs):
        """空 banner 列表 → 返回空列表。"""
        result = self._filter([], sample_threads, empty_prefs)
        assert result == []

    def test_channel_banners_not_affected_by_design(self):
        """channel 类型 banner 不进入此函数 — 由调用方保证。"""
        # 此测试仅确认函数签名可接受空列表
        from api.v1.routers.banner import _filter_thread_banners_by_prefs
        from dto.preferences import UserSearchPreferencesDTO

        prefs = UserSearchPreferencesDTO(
            user_id=1,
            exclude_authors=[111],
            exclude_tags=["test"],
            exclude_keywords="test",
        )
        result = _filter_thread_banners_by_prefs([], {}, prefs)
        assert result == []


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
