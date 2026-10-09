"""Banner 新归属、Bot 限制及 PostgreSQL 审核事务回归。"""

import asyncio
import importlib.util
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import inspect, select

from banner.banner_service import BannerService
from banner.dto.banner_approval_result import BannerApprovalResult
from core.banner_application_repository import BannerApplicationRepository
from models import BannerApplication, BannerCarousel, BannerWaitlist, Channel, Thread
from shared.time_utils import utc_now

OWNER = 987654321098765432
TARGET = 123456789012345678
COVER = "https://example.com/banner.png"


async def seed_applications(factory, *overrides):
    """创建有效目标及申请，返回会话外可使用的申请ID。"""
    async with factory() as session:
        session.add(Channel(channel_id=TARGET, guild_id=1, name="赛事"))
        session.add(
            Thread(
                thread_id=TARGET + 1,
                channel_id=42,
                guild_id=1,
                author_id=OWNER,
                title="帖子",
                thumbnail_urls=[COVER],
            )
        )
        applications = [
            BannerApplication(
                **{
                    "thread_id": TARGET,
                    "channel_id": TARGET,
                    "applicant_id": OWNER,
                    "cover_image_url": COVER,
                    "target_scope": "global",
                    "target_type": 2,
                    **override,
                }
            )
            for override in overrides
        ]
        session.add_all(applications)
        await session.flush()
        ids = [application.id for application in applications]
        await session.commit()
        return ids


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [None, 42])
@pytest.mark.parametrize("target_type", [1, 2])
@pytest.mark.parametrize(
    "state, expected",
    [
        ("active", True),
        ("waiting", True),
        ("expired", False),
        ("boundary", False),
        ("legacy_carousel", False),
        ("legacy_waitlist", False),
        ("missing_application", False),
        ("missing_applicant", False),
        ("history_pending", False),
        ("history_approved", False),
        ("history_rejected", False),
        ("other_applicant", False),
    ],
)
async def test_ongoing_ownership(
    db_session_factory, monkeypatch, scope, target_type, state, expected
):
    """只统计跨范围、跨类型的有效新归属，忽略旧记录、历史及过期项。"""
    now = utc_now()
    monkeypatch.setattr("banner.banner_service.utc_now", lambda: now)
    status = (
        state.removeprefix("history_") if state.startswith("history_") else "approved"
    )
    (application_id,) = await seed_applications(db_session_factory, {"status": status})
    async with db_session_factory() as session:
        if not state.startswith("history_"):
            data = dict(
                thread_id=TARGET,
                channel_id=scope,
                title="Banner",
                target_type=target_type,
                cover_image_url=COVER,
                application_id=application_id,
                applicant_id=OWNER,
            )
            if state.startswith("legacy_"):
                data.update(application_id=None, applicant_id=None)
            if state == "missing_application":
                data["application_id"] = None
            if state == "missing_applicant":
                data["applicant_id"] = None
            if state == "other_applicant":
                data["applicant_id"] = OWNER + 1
            if "waitlist" in state or state == "waiting":
                session.add(BannerWaitlist(**data))
            else:
                delta = {"expired": -1, "boundary": 0}.get(state, 1)
                session.add(
                    BannerCarousel(**data, end_time=now + timedelta(seconds=delta))
                )
            await session.commit()
        assert await BannerService(session).has_ongoing_banner(OWNER) is expected


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting", [False, True])
@pytest.mark.parametrize("enforce", [False, True])
async def test_bot_limit_and_api_default(db_session_factory, waiting, enforce):
    """Bot 限制真实占位，API 默认允许已有 Banner 的用户继续提交。"""
    (app_id,) = await seed_applications(db_session_factory, {"status": "approved"})
    async with db_session_factory() as session:
        values = dict(
            thread_id=TARGET, title="已有", application_id=app_id, applicant_id=OWNER
        )
        row = (
            BannerWaitlist(**values)
            if waiting
            else BannerCarousel(**values, end_time=utc_now() + timedelta(days=1))
        )
        session.add(row)
        await session.commit()
        kwargs = {"enforce_applicant_limit": True} if enforce else {}
        result = await BannerService(session).validate_and_create_application(
            TARGET, 1, OWNER, COVER, "42", **kwargs
        )
        assert result.success is not enforce
        if enforce:
            assert result.message == BannerService.APPLICANT_LIMIT_MESSAGE
        count = len((await session.execute(select(BannerApplication))).scalars().all())
        assert count == (1 if enforce else 2)


@pytest.mark.asyncio
async def test_api_route_retains_submission(db_session_factory, monkeypatch):
    """真实 API 路由不启用 Bot 限制，也不增加公开请求字段。"""
    from api.v1.routers import banner as router
    from api.v1.schemas.banner import BannerApplicationRequest

    (app_id,) = await seed_applications(db_session_factory, {"status": "approved"})
    async with db_session_factory() as session:
        session.add(
            BannerWaitlist(
                thread_id=TARGET,
                title="已有",
                application_id=app_id,
                applicant_id=OWNER,
            )
        )
        await session.commit()
    monkeypatch.setattr(router, "async_session_factory", db_session_factory)
    monkeypatch.setattr(router, "main_guild_id", 1)
    monkeypatch.setattr(router, "bot_token", "")
    monkeypatch.setattr(router, "banner_config", None)
    result = await router.apply_banner(
        BannerApplicationRequest(
            thread_link=str(TARGET), cover_image_url=COVER, target_scope="global"
        ),
        {"id": str(OWNER)},
    )
    assert result.success
    assert result.application_id != app_id


@pytest.mark.asyncio
async def test_multiple_pending_allowed(db_session_factory):
    """只有待审核申请时，Bot 仍可重复提交，批准一个才拒绝其他申请。"""
    (first_id,) = await seed_applications(db_session_factory, {})
    async with db_session_factory() as session:
        result = await BannerService(session).validate_and_create_application(
            TARGET, 1, OWNER, COVER, "42", enforce_applicant_limit=True
        )
        assert result.success
        second_id = result.application.id
        approval = await BannerService(session).approve_application(first_id, 456)
    assert [item.id for item in approval.auto_rejected] == [second_id]


@pytest.mark.asyncio
@pytest.mark.parametrize("full", [False, True])
@pytest.mark.parametrize("target_type", [1, 2])
async def test_approval_rejects_only_other_pending(
    db_session_factory, full, target_type
):
    """批准和批量拒绝原子执行，保留已审核历史及其他申请人的记录。"""
    chosen, pending_a, pending_b, approved, rejected, other = await seed_applications(
        db_session_factory,
        {
            "target_type": target_type,
            "thread_id": TARGET + 1 if target_type == 1 else TARGET,
        },
        {"target_scope": "42", "target_type": 1, "thread_id": TARGET + 1},
        {},
        {"status": "approved"},
        {"status": "rejected"},
        {"applicant_id": OWNER + 1},
    )
    async with db_session_factory() as session:
        if full:
            session.add_all(
                [
                    BannerCarousel(
                        thread_id=i,
                        title="占位",
                        end_time=utc_now() + timedelta(days=1),
                    )
                    for i in range(3)
                ]
            )
            await session.commit()
        result = await BannerService(session).approve_application(chosen, 456)
    assert result.application.id == chosen
    assert result.application.status == "approved"
    assert result.entered_carousel is not full
    assert {app.id for app in result.auto_rejected} == {pending_a, pending_b}
    assert all(app.reviewer_id == 456 for app in result.auto_rejected)
    assert all(
        app.reviewed_at == result.application.reviewed_at
        for app in result.auto_rejected
    )
    assert all(f"#{chosen}" in app.reject_reason for app in result.auto_rejected)
    async with db_session_factory() as session:
        applications = (
            (await session.execute(select(BannerApplication))).scalars().all()
        )
        states = {app.id: app.status for app in applications}
        assert states == {
            chosen: "approved",
            pending_a: "rejected",
            pending_b: "rejected",
            approved: "approved",
            rejected: "rejected",
            other: "pending",
        }
        model = BannerWaitlist if full else BannerCarousel
        row = (
            await session.execute(select(model).where(model.application_id == chosen))
        ).scalar_one()
        assert row.applicant_id == OWNER
        assert row.target_type == target_type
        with pytest.raises(ValueError, match="已被处理"):
            await BannerService(session).approve_application(pending_a, 789)
        with pytest.raises(ValueError, match="已被处理"):
            await BannerService(session).reject_application(chosen, 789, "覆盖")


@pytest.mark.asyncio
async def test_can_approve_when_already_occupied(db_session_factory):
    """已有新归属占位仍可批准后续 API 申请，自动拒绝只影响待审核项。"""
    old, chosen, sibling = await seed_applications(
        db_session_factory, {"status": "approved"}, {}, {}
    )
    async with db_session_factory() as session:
        session.add(
            BannerCarousel(
                thread_id=TARGET,
                title="已有",
                application_id=old,
                applicant_id=OWNER,
                end_time=utc_now() + timedelta(days=1),
            )
        )
        await session.commit()
        result = await BannerService(session).approve_application(chosen, 456)
        rows = (await session.execute(select(BannerCarousel))).scalars().all()
    assert {row.application_id for row in rows} == {old, chosen}
    assert [item.id for item in result.auto_rejected] == [sibling]


@pytest.mark.asyncio
@pytest.mark.parametrize("legacy", [False, True])
async def test_promotion_carries_ownership(db_session_factory, legacy):
    """候补晋升保留新归属，旧候补晋升仍留空并享受过渡。"""
    (app_id,) = await seed_applications(db_session_factory, {"status": "approved"})
    async with db_session_factory() as session:
        session.add(
            BannerCarousel(
                thread_id=99, title="过期", end_time=utc_now() - timedelta(seconds=1)
            )
        )
        session.add(
            BannerWaitlist(
                thread_id=TARGET,
                title="等待",
                application_id=None if legacy else app_id,
                applicant_id=None if legacy else OWNER,
            )
        )
        await session.commit()
        service = BannerService(session)
        assert await service.cleanup_expired_banners() == 1
        row = (await session.execute(select(BannerCarousel))).scalar_one()
        assert row.application_id == (None if legacy else app_id)
        assert row.applicant_id == (None if legacy else OWNER)
        assert await service.has_ongoing_banner(OWNER) is not legacy
        assert (await service.delete_banner_by_thread(TARGET)).success
        assert not await service.has_ongoing_banner(OWNER)


@pytest.mark.asyncio
async def test_failed_commit_rolls_back_batch(db_session_factory, monkeypatch):
    """提交失败时批准、轮播插入和自动拒绝一起回滚。"""
    chosen, sibling = await seed_applications(db_session_factory, {}, {})
    async with db_session_factory() as session:
        monkeypatch.setattr(
            session, "commit", AsyncMock(side_effect=RuntimeError("commit failed"))
        )
        with pytest.raises(RuntimeError, match="commit failed"):
            await BannerService(session).approve_application(chosen, 456)
    async with db_session_factory() as session:
        applications = (
            (await session.execute(select(BannerApplication))).scalars().all()
        )
        assert all(
            app.status == "pending" and app.reviewed_at is None for app in applications
        )
        assert not (await session.execute(select(BannerCarousel))).scalars().all()
        assert not (await session.execute(select(BannerWaitlist))).scalars().all()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "same_id, second_action", [(True, "approve"), (False, "approve"), (False, "reject")]
)
async def test_concurrent_review_refreshes_locked_state(
    db_session_factory, monkeypatch, same_id, second_action
):
    """两个会话先读到 pending 后竞争锁，后到审核不能覆盖已提交结果。"""
    chosen, sibling = await seed_applications(db_session_factory, {}, {})
    second_entered = asyncio.Event()
    original = BannerApplicationRepository.lock_applicant
    calls = 0

    async def gated_lock(repository, applicant_id):
        """让第二个会话在首个事务提交前读取待审核状态。"""
        nonlocal calls
        calls += 1
        first = calls == 1
        if not first:
            second_entered.set()
        await original(repository, applicant_id)
        if first:
            await asyncio.wait_for(second_entered.wait(), 5)

    monkeypatch.setattr(BannerApplicationRepository, "lock_applicant", gated_lock)

    async def review(application_id, action):
        """用独立数据库事务执行一次审核。"""
        async with db_session_factory() as session:
            service = BannerService(session)
            if action == "approve":
                return await service.approve_application(application_id, 456)
            return await service.reject_application(application_id, 789, "手工拒绝")

    results = await asyncio.wait_for(
        asyncio.gather(
            review(chosen, "approve"),
            review(chosen if same_id else sibling, second_action),
            return_exceptions=True,
        ),
        10,
    )
    assert sum(isinstance(result, BannerApprovalResult) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    async with db_session_factory() as session:
        rows = (await session.execute(select(BannerCarousel))).scalars().all()
        assert len(rows) == 1
        application = await BannerApplicationRepository(session).get_by_id(sibling)
        assert application.status == "rejected"
        assert application.reviewer_id == 456
        assert "自动拒绝" in application.reject_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("create_first", [False, True])
@pytest.mark.parametrize("enforce", [False, True])
async def test_creation_serializes_with_approval(
    db_session_factory, monkeypatch, create_first, enforce
):
    """创建先提交则纳入同批拒绝；批准先提交则 Bot 拒绝、API 仍允许创建。"""
    (chosen,) = await seed_applications(db_session_factory, {})
    second_entered = asyncio.Event()
    first_locked = asyncio.Event()
    original = BannerApplicationRepository.lock_applicant
    calls = 0

    async def gated_lock(repository, applicant_id):
        """固定事务先后，覆盖创建与批准两种竞争方向。"""
        nonlocal calls
        calls += 1
        first = calls == 1
        if not first:
            second_entered.set()
        await original(repository, applicant_id)
        if first:
            first_locked.set()
            await asyncio.wait_for(second_entered.wait(), 5)

    monkeypatch.setattr(BannerApplicationRepository, "lock_applicant", gated_lock)

    async def create():
        """在独立会话中提交申请并返回稳定结果。"""
        async with db_session_factory() as session:
            result = await BannerService(session).validate_and_create_application(
                TARGET, 1, OWNER, COVER, "42", enforce_applicant_limit=enforce
            )
            return result.success, result.application.id if result.application else None

    async def approve():
        """在独立会话中批准原申请。"""
        async with db_session_factory() as session:
            return await BannerService(session).approve_application(chosen, 456)

    first = asyncio.create_task(create() if create_first else approve())
    await asyncio.wait_for(first_locked.wait(), 5)
    second = asyncio.create_task(approve() if create_first else create())
    first_result, second_result = await asyncio.wait_for(
        asyncio.gather(first, second), 10
    )
    creation, approval = (
        (first_result, second_result) if create_first else (second_result, first_result)
    )
    success, new_id = creation
    assert success is (create_first or not enforce)
    assert [app.id for app in approval.auto_rejected] == (
        [new_id] if create_first else []
    )
    async with db_session_factory() as session:
        if new_id is not None:
            application = await BannerApplicationRepository(session).get_by_id(new_id)
            assert application.status == ("rejected" if create_first else "pending")


@pytest.mark.asyncio
async def test_ownership_migration_preserves_old_rows(db_session_factory):
    """实际 PostgreSQL 升降级保留旧记录并创建可空列、外键及申请人索引。"""
    spec = importlib.util.spec_from_file_location(
        "banner_ownership_migration",
        Path(__file__).parents[1] / "alembic/versions/add_banner_ownership.py",
    )
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    async with db_session_factory() as session:
        session.add(
            BannerCarousel(
                thread_id=1, title="旧轮播", end_time=utc_now() + timedelta(days=1)
            )
        )
        session.add(BannerWaitlist(thread_id=2, title="旧等待"))
        await session.commit()
        connection = await session.connection()

        def migrate(connection):
            """在测试 schema 中模拟旧表再升级，不触及应用 schema。"""
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade()
                migration.upgrade()
            inspector = inspect(connection)
            for table in ("banner_carousel", "banner_waitlist"):
                columns = {
                    column["name"]: column for column in inspector.get_columns(table)
                }
                assert columns["application_id"]["nullable"]
                assert columns["applicant_id"]["nullable"]
                assert any(
                    index["column_names"] == ["applicant_id"]
                    for index in inspector.get_indexes(table)
                )
                assert any(
                    fk["constrained_columns"] == ["application_id"]
                    for fk in inspector.get_foreign_keys(table)
                )

        await connection.run_sync(migrate)
        await session.commit()
        carousel = (await session.execute(select(BannerCarousel))).scalar_one()
        waitlist = (await session.execute(select(BannerWaitlist))).scalar_one()
        assert (carousel.title, carousel.application_id, carousel.applicant_id) == (
            "旧轮播",
            None,
            None,
        )
        assert (waitlist.title, waitlist.application_id, waitlist.applicant_id) == (
            "旧等待",
            None,
            None,
        )
