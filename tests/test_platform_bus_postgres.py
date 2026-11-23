"""验证平台服务在真实 PostgreSQL 上的事务与投影。"""

import json
import os
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from models.booklist import Booklist
from models.booklist_item import BooklistItem
from models.thread import Thread
from models.user_collection import UserCollection
from models.work import Work
from models.work_growth_daily import WorkGrowthDaily
from platform_bus.bindings import apply_bindings
from platform_bus.outbox import enqueue_work
from platform_bus.projections import apply_projection
from platform_bus.worker import deliver, handle
from platform_gateway.principal import Principal
from platform_gateway.work_api_service import WorkApiService
from platform_gateway.work_interaction_service import WorkInteractionService
from shared.time_utils import utc_now

URL = os.environ.get("TEST_WORK_DB_URL")
pytestmark = pytest.mark.skipif(not URL, reason="需要已迁移的独立测试库")


@pytest.mark.asyncio
async def test_purge_and_restore_out_of_order(sessions):
    """永久删除不能被迟到的恢复覆盖，软删除不能覆盖更新的恢复。"""
    events = (
        ("work.restored", "restoredAt", 3, "visible"),
        ("work.deleted", "deletedAt", 2, "visible"),
        ("work.purged", "purgedAt", 1, "purged"),
        ("work.restored", "restoredAt", 4, "purged"),
    )
    async with sessions.begin() as session:
        for name, field, second, expected in events:
            await apply_projection(
                session,
                "collections",
                {
                    "type": name,
                    "data": {
                        "workId": "9123456",
                        field: f"2026-11-01T00:00:0{second}Z",
                    },
                },
            )
            state = await session.scalar(
                text("""
                SELECT state FROM platform_work_projection WHERE consumer='collections' AND work_id=9123456""")
            )
            assert state == expected


@pytest.mark.asyncio
async def test_discord_source_uses_binding_and_original_url(sessions):
    """旧 Discord 写入者仅传来源身份，也能读取作品归属并发布事件。"""
    async with sessions.begin() as session:
        await apply_bindings(
            session,
            {
                "userId": "fixture-d",
                "version": "1",
                "currentDiscordId": "809122",
                "historicalDiscordIds": ["809121"],
            },
        )
        for discord in (809121, 809122):
            thread = Thread(
                thread_id=discord + 10000,
                author_id=discord,
                guild_id=901,
                channel_id=902,
                title="旧格式的论坛帖子",
            )
            session.add(thread)
            await session.flush()
            await session.refresh(thread)
            work = await session.get(Work, thread.work_id)
            assert work.author_id == "fixture-d"
            assert (
                work.main_ref == f"https://discord.com/channels/901/{thread.thread_id}"
            )
            assert (
                await session.execute(
                    text("""
                SELECT kind FROM platform_outbox WHERE work_id=:id"""),
                    {"id": work.id},
                )
            ).scalars().all() == ["work.updated"]
            # 旧写入端依然只更新帖子表，作品标题编辑应推进更新时间。
            await session.execute(
                text("UPDATE thread SET title='修改来源标题' WHERE id=:id"),
                {"id": thread.id},
            )
            await session.refresh(work)
            assert work.title == "修改来源标题"
            assert work.updated_at is not None
            edited_at = work.updated_at

            # 纯统计写入不应刷新作品的资料更新时间。
            await session.execute(
                text("UPDATE thread SET reaction_count=13 WHERE id=:id"),
                {"id": thread.id},
            )
            await session.refresh(work)
            assert work.updated_at == edited_at


@pytest_asyncio.fixture
async def sessions():
    """每个测试拥有自己的外层回滚事务。"""
    engine = create_async_engine(URL)
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            yield async_sessionmaker(
                conn, expire_on_commit=False, join_transaction_mode="create_savepoint"
            )
        finally:
            await outer.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_historical_private_ownership(sessions):
    """换绑保留旧书单，重复收藏只认领一次，旧事件不能回退绑定。"""
    snapshot = {
        "userId": "fixture-b",
        "version": "3",
        "currentDiscordId": "802002",
        "historicalDiscordIds": ["802001"],
    }
    async with sessions.begin() as session:
        for discord in (802001, 802002):
            booklist = Booklist(
                owner_id=discord,
                title=str(discord),
                is_default=True,
                is_public=False,
            )
            session.add(booklist)
            await session.flush()
            for wid in (880001, discord):
                session.add(
                    BooklistItem(
                        booklist_id=booklist.id,
                        owner_id=discord,
                        work_id=wid,
                        comment=f"{discord}-{wid}",
                    )
                )
            session.add(
                UserCollection(user_id=discord, target_type=2, target_id=803000)
            )
        await session.flush()
        await apply_bindings(session, snapshot)
        await apply_bindings(session, snapshot)
        await apply_bindings(
            session,
            {
                **snapshot,
                "version": "2",
                "currentDiscordId": "802001",
                "historicalDiscordIds": [],
            },
        )
        rows = (
            await session.execute(
                text("""
            SELECT discord_user_id,is_current FROM discord_identity WHERE user_id='fixture-b'
            ORDER BY discord_user_id""")
            )
        ).all()
        assert rows == [("802001", False), ("802002", True)]
        assert (
            await session.execute(
                text("""
            SELECT count(*),count(*) FILTER(WHERE is_default) FROM booklist
            WHERE fluxer_owner_id='fixture-b' AND NOT is_public""")
            )
        ).one() == (2, 1)
        assert (
            await session.execute(
                text("""
            SELECT count(*) FROM booklist_item i JOIN booklist b ON b.id=i.booklist_id
            WHERE b.is_default AND b.fluxer_owner_id='fixture-b'""")
            )
        ).scalar_one() == 3
        assert (
            await session.execute(
                text("""
            SELECT count(*) FROM user_collection WHERE fluxer_user_id='fixture-b'""")
            )
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_chat_delivery_order(sessions):
    """同一消息按真正的变更时间更新回复数。"""
    async with sessions.begin() as session:
        work = Work(title="事件投影测试", source_platform="platform")
        session.add(work)
        await session.flush()
        wid = work.id
    for kind, key, second in [
        ("deleted", "deletedAt", 2),
        ("created", "createdAt", 1),
        ("restored", "restoredAt", 3),
        ("restored", "restoredAt", 3),
    ]:
        name = "chat.message_" + kind
        event = {
            "type": name,
            "eventId": "fixture-test-" + kind,
            "source": "chat",
            "data": {
                "workId": str(wid),
                "messageId": "fixture-m1",
                key: f"2026-01-01T00:00:0{second}Z",
            },
        }
        await handle("works", "ody.events." + name, json.dumps(event), None, sessions)
    async with sessions() as session:
        assert (
            await session.execute(
                text("SELECT reply_count FROM work_statistics WHERE work_id=:id"),
                {"id": wid},
            )
        ).scalar_one() == 1


@pytest.mark.asyncio
async def test_outbox_rolls_back_with_publication(sessions):
    """写作品失败时，聊天任务和总线事件也全部撤销。"""
    async with sessions() as session:
        work = Work(title="回滚", source_platform="platform")
        session.add(work)
        await enqueue_work(session, work, "work.updated")
        wid = work.id
        assert (
            await session.execute(
                text("SELECT count(*) FROM platform_outbox WHERE work_id=:id"),
                {"id": wid},
            )
        ).scalar_one() == 2
        await session.rollback()
        assert (
            await session.execute(
                text("SELECT count(*) FROM platform_outbox WHERE work_id=:id"),
                {"id": wid},
            )
        ).scalar_one() == 0


@pytest.mark.asyncio
async def test_lost_puback_retries_same_event_id(sessions):
    """发布确认丢失后再次使用同一个消息 ID，只有成功交付才设置 sent_at。"""
    async with sessions.begin() as session:
        work = Work(title="发布确认", source_platform="platform")
        session.add(work)
        await enqueue_work(session, work, "work.purged")
        wid = work.id
    jetstream = AsyncMock()
    jetstream.publish.side_effect = [TimeoutError(), None]
    assert await deliver(jetstream, None, sessions)
    async with sessions.begin() as session:
        attempted = (
            await session.execute(
                text("""
            SELECT attempts,sent_at,event_id FROM platform_outbox WHERE work_id=:wid"""),
                {"wid": wid},
            )
        ).one()
        assert attempted.attempts == 1 and attempted.sent_at is None
        await session.execute(
            text("UPDATE platform_outbox SET available_at=now() WHERE work_id=:wid"),
            {"wid": wid},
        )
    assert await deliver(jetstream, None, sessions)
    async with sessions() as session:
        assert await session.scalar(
            text("SELECT sent_at FROM platform_outbox WHERE work_id=:wid"), {"wid": wid}
        )
    assert [
        call.kwargs["headers"]["Nats-Msg-Id"]
        for call in jetstream.publish.await_args_list
    ] == [attempted.event_id] * 2


@pytest.mark.asyncio
async def test_platform_interactions_keep_independent_viewer_state(sessions):
    """点赞、默认书单与关注的幂等写入分别映射到平台字段。"""
    async with sessions() as session:
        work = Work(
            title="平台互动验证",
            source_platform="platform",
            source_visible=True,
            published_at=utc_now(),
        )
        session.add(work)
        await session.commit()
        actor = Principal(userId="fixture-platform-reader")
        api = WorkApiService(session, actor)
        interactions = WorkInteractionService(api)
        work_id = str(work.id)
        for _ in range(2):
            await interactions.like(work_id, like=True)
            await interactions.favorite(work_id, favorite=True)
            await interactions.follow(work_id, follow=True)
        view = (await api.repository.project([work], actor))[0]
        assert view["stats"]["likes"] == 1
        assert view["stats"]["favorites"] == 1
        assert all(view["viewer"][key] for key in ("liked", "favorited", "followed"))
        assert len(view["viewer"]["inCollections"]) == 1
        assert [
            item["id"]
            for item in (
                await api.list(
                    {"q": work.title, "minFavorites": "1", "sort": "favorites"},
                    search=True,
                )
            )["items"]
        ] == [work_id]
        assert (
            await session.scalar(
                text(
                    "SELECT count(*) FROM platform_outbox WHERE work_id=:id AND kind='auto_follow'"
                ),
                {"id": work.id},
            )
            == 1
        )
        growth_key = (work.id, utc_now().date(), "reaction")
        assert (await session.get(WorkGrowthDaily, growth_key)).delta == 1

        work.rules_body = "规则"
        work.rules_version = 1
        work.rules_updated_at = utc_now()
        await session.commit()
        followed = await interactions.follows({}, only_updated=True)
        assert followed["items"][0]["hasUpdate"] is True
        assert followed["items"][0]["work"]["viewer"]["needsAck"]["rules"] is True
        await interactions.mark_viewed([work_id])
        assert (await interactions.follows({}, only_updated=True))["items"] == []

        for _ in range(2):
            await interactions.like(work_id, like=False)
            await interactions.favorite(work_id, favorite=False)
        view = (await api.repository.project([work], actor))[0]
        assert view["stats"]["likes"] == view["stats"]["favorites"] == 0
        assert view["viewer"]["followed"] is True
        assert view["viewer"]["liked"] is view["viewer"]["favorited"] is False
        assert (await session.get(WorkGrowthDaily, growth_key)).delta == 0


@pytest.mark.asyncio
async def test_every_new_message_can_follow_and_failed_preferences_retry(
    sessions, monkeypatch
):
    """关掉后重开发言可触发，偏好服务短时失败可重试，主动取关始终保留。"""
    monkeypatch.setenv("FORUM_CORE_PROFILE_TOKEN", "fixture-test-token")
    actor = Principal(userId="fixture-message-author")
    async with sessions.begin() as session:
        work = Work(
            title="交流区",
            source_platform="platform",
            source_visible=True,
            published_at=utc_now(),
        )
        session.add(work)
        await session.flush()
        work_id = str(work.id)

    async def ingest(name, number, time_field):
        """使用既有消费者校验和收件箱去重路径传入消息。"""
        event = {
            "eventId": f"fixture-auto-follow:{number}:{name}",
            "type": name,
            "source": "chat",
            "data": {
                "workId": work_id,
                "messageId": f"fixture-auto-follow:{number}",
                "authorId": actor.userId,
                time_field: f"2026-01-01T00:00:0{number}Z",
            },
        }
        await handle("works", "ody.events." + name, json.dumps(event), None, sessions)

    jetstream = AsyncMock()
    status = 200
    enabled = False

    def settings(request):
        """在相同 HTTP 客户端接口模拟权威偏好与暂时故障。"""
        assert request.url.path == f"/internal/users/{actor.userId}/follow-settings"
        return httpx.Response(
            status, json={"userId": actor.userId, "autoFollow": enabled}
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(settings), base_url="http://core.test"
    ) as client:
        await ingest("chat.message_created", 1, "createdAt")
        assert await deliver(jetstream, client, sessions)
        await ingest("chat.message_created", 1, "createdAt")
        await ingest("chat.message_restored", 2, "restoredAt")
        assert not await deliver(jetstream, client, sessions)

        await ingest("chat.message_created", 3, "createdAt")
        enabled = True
        status = 503
        assert await deliver(jetstream, client, sessions)
        async with sessions.begin() as session:
            assert (
                await session.scalar(
                    text(
                        "SELECT attempts FROM platform_outbox WHERE kind='auto_follow' AND sent_at IS NULL"
                    )
                )
                == 1
            )
            await session.execute(
                text(
                    "UPDATE platform_outbox SET available_at=now() WHERE kind='auto_follow' AND sent_at IS NULL"
                )
            )
        status = 200
        assert await deliver(jetstream, client, sessions)

        async with sessions() as session:
            api = WorkApiService(session, actor)
            interactions = WorkInteractionService(api)
            assert (await interactions.follows({}, only_updated=False))["items"][0][
                "work"
            ]["id"] == work_id
            await interactions.follow(work_id, follow=False)
            await interactions.follow(work_id, follow=True)
            row = (
                await session.execute(
                    text(
                        "SELECT active_flag,manual_unfollowed_at FROM thread_follow WHERE work_id=:id"
                    ),
                    {"id": int(work_id)},
                )
            ).one()
            assert row.active_flag and row.manual_unfollowed_at
            await interactions.follow(work_id, follow=False)
        await ingest("chat.message_created", 4, "createdAt")
        assert not await deliver(jetstream, client, sessions)
        jetstream.publish.assert_not_awaited()
