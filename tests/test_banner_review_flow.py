"""Bot 入口、自动拒绝通知及审核消息延迟投递回归。"""

import json
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from banner.banner_review_message_service import BannerReviewMessageService
from banner.banner_review_notification_service import BannerReviewNotificationService
from banner.banner_service import BannerService
from banner.dto.banner_approval_result import BannerApprovalResult
from banner.dto.banner_review_application import BannerReviewApplication
from banner.listeners.banner_event_listener import BannerEventListener
from banner.views.banner_application_button_view import BannerApplicationButtonView
from banner.views.review_embed_builder import ReviewEmbedBuilder
from banner.views.review_view import ReviewView
from core.banner_application_repository import BannerApplicationRepository
from models import BannerApplication, Channel
from shared.redis_client import RedisManager

TARGET = 123456789012345678
OWNER = 987654321098765432


def application_snapshot(application_id=1, status="rejected"):
    """构造独立审核快照，覆盖无数据库会话的 Discord I/O。"""
    return BannerReviewApplication.from_application(
        BannerApplication(
            id=application_id,
            thread_id=TARGET,
            channel_id=TARGET,
            applicant_id=OWNER,
            target_type=2,
            target_scope="global",
            cover_image_url="https://example.com/cover.png",
            status=status,
            reviewer_id=456,
            reject_reason="申请 #9 已通过，本申请自动拒绝。",
            review_thread_id=888,
            review_message_id=999 + application_id,
        )
    )


def mock_factory():
    """构造异步数据库会话工厂。"""
    factory = MagicMock()
    factory.return_value.__aenter__.return_value = AsyncMock()
    return factory


@pytest.mark.asyncio
@pytest.mark.parametrize("ongoing", [False, True])
async def test_application_button_preflight(monkeypatch, ongoing):
    """按钮点击立即检查，有占位时不打开表单并仅向本人提示。"""
    check = AsyncMock(return_value=ongoing)
    monkeypatch.setattr(BannerService, "has_ongoing_banner", check)
    view = BannerApplicationButtonView([], mock_factory())
    interaction = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = OWNER
    interaction.user.roles = []
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    await view.application_button.callback(interaction)
    check.assert_awaited_once_with(OWNER)
    if ongoing:
        interaction.response.send_modal.assert_not_awaited()
        interaction.response.send_message.assert_awaited_once_with(
            f"❌ {BannerService.APPLICANT_LIMIT_MESSAGE}", ephemeral=True
        )
    else:
        interaction.response.send_modal.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_button_preserves_role_check(monkeypatch):
    """没有申请角色时仍先拒绝，不访问资格查询。"""
    check = AsyncMock(return_value=False)
    monkeypatch.setattr(BannerService, "has_ongoing_banner", check)
    view = BannerApplicationButtonView([123], mock_factory())
    interaction = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.roles = []
    interaction.response.send_message = AsyncMock()
    await view.application_button.callback(interaction)
    check.assert_not_awaited()
    assert interaction.response.send_message.call_args.kwargs["ephemeral"]


@pytest.mark.asyncio
async def test_button_query_failure_prompts_retry(monkeypatch):
    """资格查询失败时不放行，并提示重试。"""
    monkeypatch.setattr(
        BannerService, "has_ongoing_banner", AsyncMock(side_effect=TimeoutError)
    )
    view = BannerApplicationButtonView([], mock_factory())
    interaction = MagicMock()
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.roles = []
    interaction.response.send_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    await view.application_button.callback(interaction)
    interaction.response.send_modal.assert_not_awaited()
    assert "重试" in interaction.response.send_message.call_args.args[0]


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["form", "apply"])
async def test_bot_rechecks_on_submission(monkeypatch, entry):
    """表单及最终创建入口均拒绝已有占位，不创建记录或发送审核消息。"""
    monkeypatch.setattr(
        BannerService, "has_ongoing_banner", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(BannerApplicationRepository, "lock_applicant", AsyncMock())
    create = AsyncMock()
    validate = AsyncMock()
    publish = AsyncMock()
    monkeypatch.setattr(BannerApplicationRepository, "create", create)
    monkeypatch.setattr(BannerService, "validate_application_request", validate)
    monkeypatch.setattr(BannerReviewMessageService, "send", publish)
    listener = BannerEventListener(MagicMock(), mock_factory(), {})
    interaction = MagicMock()
    interaction.user.id = OWNER
    interaction.followup.send = AsyncMock()
    if entry == "form":
        await listener.on_banner_form_submit(interaction, TARGET, None, 1)
    else:
        await listener.on_banner_apply(
            interaction, TARGET, None, "global", OWNER, TARGET, 1
        )
    create.assert_not_awaited()
    validate.assert_not_awaited()
    publish.assert_not_awaited()
    interaction.followup.send.assert_awaited_once_with(
        f"❌ {BannerService.APPLICANT_LIMIT_MESSAGE}", ephemeral=True
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, "message", "dm", "archive"])
async def test_notification_failure_isolation(monkeypatch, failure):
    """每个 Discord 后续独立执行，失败不阻断剩余私信或归档。"""
    application = application_snapshot()
    message = MagicMock()
    message.embeds = [discord.Embed(title="申请")]
    message.edit = AsyncMock(
        side_effect=RuntimeError("edit failed") if failure == "message" else None
    )
    user = MagicMock()
    user.send = AsyncMock(
        side_effect=RuntimeError("dm failed") if failure == "dm" else None
    )
    bot = MagicMock()
    bot.fetch_user = AsyncMock(return_value=user)
    archive = AsyncMock(
        side_effect=RuntimeError("archive failed") if failure == "archive" else None
    )
    monkeypatch.setattr(ReviewEmbedBuilder, "archive_review", archive)
    await BannerReviewNotificationService(bot, {}).notify(application, message=message)
    message.edit.assert_awaited_once()
    user.send.assert_awaited_once()
    archive.assert_awaited_once()
    if failure != "message":
        assert message.edit.call_args.kwargs["view"] is None
        fields = {
            field.name: field.value
            for field in message.edit.call_args.kwargs["embed"].fields
        }
        assert fields["拒绝理由"] == application.reject_reason
    assert (
        user.send.call_args.kwargs["embed"].fields[0].value == application.reject_reason
    )


@pytest.mark.asyncio
async def test_approve_listener_notifies_every_result_after_session(monkeypatch):
    """事件监听器提交后通知通过项及所有拒绝项，并反馈自动拒绝数量。"""
    approved = application_snapshot(status="approved")
    siblings = [application_snapshot(2), application_snapshot(3)]
    result = BannerApprovalResult(approved, False, siblings)
    record = BannerApplication(
        id=1,
        thread_id=TARGET,
        channel_id=TARGET,
        applicant_id=OWNER,
        target_scope="global",
    )
    monkeypatch.setattr(
        BannerService,
        "get_application_by_review_message",
        AsyncMock(return_value=record),
    )
    monkeypatch.setattr(
        BannerService, "approve_application", AsyncMock(return_value=result)
    )
    active_session = False
    factory = mock_factory()

    async def enter():
        """标记数据库会话正在使用。"""
        nonlocal active_session
        active_session = True
        return AsyncMock()

    async def leave(*args):
        """标记会话已关闭。"""
        nonlocal active_session
        active_session = False

    factory.return_value.__aenter__.side_effect = enter
    factory.return_value.__aexit__.side_effect = leave

    async def notify(*args, **kwargs):
        """确保通知执行时已经离开数据库会话。"""
        assert not active_session

    notify_mock = AsyncMock(side_effect=notify)
    monkeypatch.setattr(BannerReviewNotificationService, "notify", notify_mock)
    interaction = MagicMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    listener = BannerEventListener(MagicMock(), factory, {})
    await listener.on_banner_review_approve(interaction, 456)
    assert [call.args[0].id for call in notify_mock.await_args_list] == [1, 2, 3]
    assert "自动拒绝其他 2 个" in interaction.followup.send.call_args.args[0]


async def seed_delivery(factory, status="pending", siblings=False):
    """创建没有审核消息的申请，模拟 Redis 等待投递。"""
    async with factory() as session:
        session.add(Channel(channel_id=TARGET, guild_id=1, name="赛事"))
        applications = [
            BannerApplication(
                thread_id=TARGET,
                channel_id=TARGET,
                applicant_id=OWNER,
                target_scope="global",
                target_type=2,
                cover_image_url="https://example.com/cover.png",
                status=status,
                reviewer_id=456 if status != "pending" else None,
                reject_reason="自动拒绝" if status == "rejected" else None,
            )
            for _ in range(2 if siblings else 1)
        ]
        session.add_all(applications)
        await session.flush()
        ids = [application.id for application in applications]
        await session.commit()
        return ids


def delivery_bot():
    """构造可发送及修改审核消息的 Discord 帖子。"""
    message = MagicMock(id=777)
    message.edit = AsyncMock()
    channel = MagicMock(spec=discord.Thread)
    channel.send = AsyncMock(return_value=message)
    bot = MagicMock()
    bot.fetch_channel = AsyncMock(return_value=channel)
    return bot, channel, message


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", "approved", "rejected"])
async def test_delayed_review_delivery_has_current_state(db_session_factory, status):
    """已经审核的申请延迟投递时显示结果，不再生成可操作审核按钮。"""
    (application_id,) = await seed_delivery(db_session_factory, status)
    bot, channel, message = delivery_bot()
    latest = await BannerReviewMessageService(
        bot, db_session_factory, {"review_thread_id": 888}
    ).send(application_id)
    assert latest.status == status
    assert latest.review_message_id == 777
    if status == "pending":
        assert isinstance(channel.send.call_args.kwargs["view"], ReviewView)
        message.edit.assert_not_awaited()
    else:
        assert channel.send.call_args.kwargs["view"] is None
        assert message.edit.call_args.kwargs["view"] is None
        fields = {
            field.name: field.value
            for field in message.edit.call_args.kwargs["embed"].fields
        }
        assert "审核结果" in fields
        if status == "rejected":
            assert fields["拒绝理由"] == "自动拒绝"


@pytest.mark.asyncio
async def test_review_during_message_send_is_reconciled(db_session_factory):
    """投递期间被另一个申请的批准自动拒绝，回填后移除已发送的按钮。"""
    victim, winner = await seed_delivery(db_session_factory, siblings=True)
    bot, channel, message = delivery_bot()

    async def send_then_approve(**kwargs):
        """在 Discord 消息发送与数据库回填之间插入审核事务。"""
        assert isinstance(kwargs["view"], ReviewView)
        async with db_session_factory() as session:
            result = await BannerService(session).approve_application(winner, 456)
            assert [item.id for item in result.auto_rejected] == [victim]
            assert result.auto_rejected[0].review_message_id is None
        return message

    channel.send.side_effect = send_then_approve
    latest = await BannerReviewMessageService(
        bot, db_session_factory, {"review_thread_id": 888}
    ).send(victim)
    assert latest.status == "rejected"
    assert latest.review_message_id == 777
    message.edit.assert_awaited_once()
    assert message.edit.call_args.kwargs["view"] is None
    assert any(
        f"#{winner}" in str(field.value)
        for field in message.edit.call_args.kwargs["embed"].fields
    )


@pytest.mark.asyncio
async def test_redis_consumer_uses_final_state(db_session_factory, monkeypatch):
    """真实 Redis 消费事件对已自动拒绝的记录使用最终状态投递。"""
    (application_id,) = await seed_delivery(db_session_factory, "rejected")
    redis = MagicMock()
    redis.brpop = AsyncMock(
        return_value=(
            "banner:review:queue",
            json.dumps({"application_id": application_id}),
        )
    )
    monkeypatch.setattr(RedisManager, "get_client", lambda: redis)
    bot, channel, message = delivery_bot()
    listener = BannerEventListener(bot, db_session_factory, {"review_thread_id": 888})
    await listener._consume_banner_review_queue.coro(listener)
    channel.send.assert_awaited_once()
    assert channel.send.call_args.kwargs["view"] is None
    assert message.edit.call_args.kwargs["view"] is None


def test_review_result_is_idempotent():
    """重复同步终态只保留一组结果字段，不破坏原申请内容。"""
    application = application_snapshot()
    embed = discord.Embed(title="原申请")
    embed.add_field(name="申请人", value=str(OWNER))
    ReviewEmbedBuilder.apply_review_result(embed, application)
    ReviewEmbedBuilder.apply_review_result(
        embed, replace(application, reject_reason="新理由")
    )
    assert [field.name for field in embed.fields] == ["申请人", "审核结果", "拒绝理由"]
    assert embed.fields[-1].value == "新理由"
