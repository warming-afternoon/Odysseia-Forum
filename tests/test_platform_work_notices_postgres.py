"""回归验证平台公告与规则使用本地通知和作品确认记录。"""

# ruff: noqa: F401, F811
from uuid import uuid4

from sqlalchemy import func, select

from dto.work.work_actor_dto import WorkActorDTO
from dto.work.work_command_qo import WorkCommandQo
from models.discord_identity import DiscordIdentity
from models.notification import Notification
from models.thread_follow import ThreadFollow
from models.work import Work
from models.work_ack import WorkAck
from models.work_notification_outbox import WorkNotificationOutbox
from shared.time_utils import utc_now
from test_platform_http_postgres import platform_http, pytestmark, signed
from work.work_ack_repository import work_ack_states
from work.work_notification_service import WorkNotificationService


async def test_platform_announcements_use_versioned_history(platform_http):
    """公告序号不受编辑、撤回、分页或幂等重试影响。"""
    client, sessions = platform_http
    owner_id = f"owner-{uuid4()}"
    reader_id = f"reader-{uuid4()}"
    owner, reader = signed(owner_id), signed(reader_id)
    async with sessions() as session:
        work = Work(
            title="公告测试",
            author_id=owner_id,
            source_platform="platform",
            published_at=utc_now(),
            rating="general",
        )
        session.add(work)
        await session.flush()
        work_id = work.id
        session.add(ThreadFollow(work_id=work_id, fluxer_user_id=reader_id))
        await session.commit()
    path = f"/api/v1/works/{work_id}"
    announcements = f"{path}/announcements"
    payload = {"body": "第一条", "images": ["https://example.com/first.png"]}
    first = await client.post(
        announcements, json=payload, headers={**owner, "Idempotency-Key": "first"}
    )
    assert first.status_code == 201, first.text
    first = first.json()
    assert first["version"] == 1 and first["revision"] == 1 and first["title"] is None
    retry = await client.post(
        announcements, json=payload, headers={**owner, "Idempotency-Key": "first"}
    )
    assert retry.json()["id"] == first["id"]
    second = await client.post(announcements, json={"body": "第二条"}, headers=owner)
    assert second.status_code == 201, second.text
    assert second.json()["version"] == 2
    page = (await client.get(announcements, headers=reader, params={"limit": 1})).json()
    assert [item["version"] for item in page["items"]] == [2]
    third = await client.post(announcements, json={"body": "第三条"}, headers=owner)
    assert third.status_code == 201, third.text
    older = await client.get(
        announcements, headers=reader, params={"cursor": page["nextCursor"]}
    )
    assert [item["version"] for item in older.json()["items"]] == [1]
    assert (
        await client.post(
            f"{path}/ack", json={"kind": "announcement", "version": 4}, headers=reader
        )
    ).status_code == 422
    assert (
        await client.post(
            f"{path}/ack", json={"kind": "announcement", "version": "3"}, headers=reader
        )
    ).status_code == 204

    first_path = f"{announcements}/{first['id']}"
    edited = await client.patch(
        first_path,
        json={"title": "新标题"},
        headers={**owner, "If-Match": '"1"'},
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["version"] == 1 and edited.json()["revision"] == 2
    stale = await client.patch(
        first_path,
        json={"title": "旧编辑"},
        headers={**owner, "If-Match": '"1"'},
    )
    assert stale.status_code == 409
    assert (await client.delete(first_path, headers=owner)).status_code == 204
    history = (await client.get(announcements, headers=reader)).json()
    assert [item["version"] for item in history["items"]] == [3, 2, 1]
    withdrawn = history["items"][-1]
    assert withdrawn["withdrawn"] and withdrawn["withdrawnAt"]
    assert (withdrawn["title"], withdrawn["body"], withdrawn["images"]) == (
        None,
        "",
        [],
    )
    async with sessions() as session:
        assert await work_ack_states(session, [work_id], reader_id) == {}
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.work_id == work_id)
            )
            == 3
        )


async def test_platform_rules_require_ack_and_clear_only_matching_notices(
    platform_http,
):
    """规则版本有独立来源编号，读者确认只推进对应作品及版本。"""
    client, sessions = platform_http
    owner_id = f"owner-{uuid4()}"
    reader_id = f"reader-{uuid4()}"
    owner, reader = (
        signed(owner_id, mode="r18", showR18G=True),
        signed(reader_id, mode="r18", showR18G=True),
    )
    author_discord = uuid4().int % (2**62)
    async with sessions() as session:
        session.add(
            DiscordIdentity(
                discord_user_id=str(author_discord),
                user_id=owner_id,
                is_current=False,
            )
        )
        works = [
            Work(
                title="规则测试",
                author_id=owner_id,
                source_platform="platform",
                published_at=utc_now(),
                rating=rating,
            )
            for rating in ("r18g", "general")
        ]
        session.add_all(works)
        await session.flush()
        work_ids = [work.id for work in works]
        for work_id in work_ids:
            session.add(ThreadFollow(work_id=work_id, fluxer_user_id=reader_id))
            session.add(ThreadFollow(work_id=work_id, user_id=author_discord))
        await session.commit()

    path, other = (f"/api/v1/works/{work_id}" for work_id in work_ids)
    assert (await client.get(f"{path}/rules")).status_code == 404
    assert (
        await client.get(f"{path}/rules", headers=signed(reader_id, mode="r18"))
    ).status_code == 404
    initial = await client.get(f"{path}/rules", headers=reader)
    assert initial.json() == {"body": "", "version": 0, "updatedAt": None}
    assert (
        await client.put(f"{path}/rules", json={"body": "越权"}, headers=reader)
    ).status_code == 403
    for base, version in ((path, 1), (other, 1), (path, 2)):
        saved = await client.put(
            f"{base}/rules", json={"body": f"第 {version} 版"}, headers=owner
        )
        assert saved.status_code == 200, saved.text
        assert saved.json()["version"] == version

    assert (
        await client.post(
            f"{path}/ack", json={"kind": "rules", "version": 1}, headers=reader
        )
    ).status_code == 204
    async with sessions() as session:
        notices = list(
            await session.scalars(
                select(Notification)
                .where(Notification.work_id.in_(work_ids))
                .order_by(Notification.id)
            )
        )
        assert (
            len(notices) == 3
            and len({notice.event_source_id for notice in notices}) == 3
        )
        assert all(
            notice.event_type == "work_rules" and notice.fluxer_user_id == reader_id
            for notice in notices
        )
        assert [notice.read_at is not None for notice in notices] == [
            True,
            False,
            False,
        ]
        assert (
            await session.scalar(
                select(func.count())
                .select_from(WorkNotificationOutbox)
                .where(WorkNotificationOutbox.recipient_id == reader_id)
            )
            == 4
        )
        assert (
            await session.get(WorkAck, (work_ids[0], owner_id, "rules"))
        ).confirmed_version == 2
        assert (await work_ack_states(session, work_ids, reader_id))[work_ids[0]][
            "rules"
        ]
    # 读者确认不会要求关注，旧请求也不会回退确认。
    nonfollower = signed(f"visitor-{uuid4()}", mode="r18", showR18G=True)
    for version in (2, 1):
        response = await client.post(
            f"{path}/ack",
            json={"kind": "rules", "version": version},
            headers=nonfollower,
        )
        assert response.status_code == 204

    # 批量标记通知已读也不会推进独立的规则确认记录。
    notification_service = WorkNotificationService(sessions)
    command = WorkCommandQo(
        actor=WorkActorDTO(id=reader_id, discord_user_id=""), values={}
    )
    page = await notification_service.notifications(offset=0, limit=20, command=command)
    assert len(page["items"]) == 1 and page["items"][0]["rules_version"] == 1
    assert (await notification_service.read_all(command))["marked_read"] == 1
    async with sessions() as session:
        pending = await work_ack_states(session, work_ids, reader_id)
        assert pending[work_ids[1]]["rules"]
