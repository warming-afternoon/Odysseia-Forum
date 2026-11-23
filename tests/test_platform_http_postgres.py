"""使用真实 PostgreSQL 验证作品、搜索、书单和聊天策略的 HTTP 合约。"""

import base64
import hmac
import json
import os
import time
from datetime import timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.v2.routers import works
from core.booklist_repository import BooklistRepository
from models.author import Author
from models.booklist import Booklist
from models.tag import Tag
from models.tag_binding import TagBinding
from models.thread import Thread
from models.work import Work
from models.work_growth_daily import WorkGrowthDaily
from models.work_statistics import WorkStatistics
from platform_bus.worker import handle
from platform_gateway.http import timestamp
from platform_gateway.routes import internal, router
from shared.time_utils import utc_now


URL = os.environ.get("TEST_WORK_DB_URL")
pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not URL, reason="需要已迁移的独立测试库"),
]


@pytest_asyncio.fixture
async def platform_http(monkeypatch):
    """在外层回滚事务中执行每次 HTTP 请求。"""
    monkeypatch.setenv("FORUM_PRINCIPAL_SECRET", "http-test-forum")
    monkeypatch.setenv("FORUM_PLATFORM_ENABLED", "true")
    monkeypatch.setenv("FORUM_INBOUND_INTERNAL_TOKEN", "http-test-internal")
    monkeypatch.setenv("TERMS_VERSION", "http-test-terms")
    monkeypatch.delenv("FORUM_CORE_PROFILE_TOKEN", raising=False)
    engine = create_async_engine(URL)
    try:
        async with engine.connect() as connection:
            outer = await connection.begin()
            try:
                sessions = async_sessionmaker(
                    connection,
                    expire_on_commit=True,
                    join_transaction_mode="create_savepoint",
                )
                monkeypatch.setattr(works, "AsyncSessionFactory", sessions)
                app = FastAPI()
                app.include_router(router)
                app.include_router(internal)
                transport = httpx.ASGITransport(app=app)
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://test"
                ) as client:
                    yield client, sessions
            finally:
                await outer.rollback()
    finally:
        await engine.dispose()


def signed(uid, **claims):
    """按网关签名实现创建当前 HTTP 请求的测试身份。"""
    principal = {
        "userId": uid,
        "mode": "general",
        "iat": time.time_ns() // 1_000_000,
        **claims,
    }
    payload = base64.urlsafe_b64encode(json.dumps(principal).encode()).rstrip(b"=")
    digest = hmac.digest(b"http-test-forum", payload, "sha256")
    signature = base64.urlsafe_b64encode(digest).rstrip(b"=")
    return {"X-Ody-Principal": (payload + b"." + signature).decode()}


async def test_work_interactions_respect_gateway_visibility(platform_http):
    """平台路由返回空成功响应，并按签名模式过滤当前关注。"""
    client, sessions = platform_http
    async with sessions.begin() as session:
        work = Work(
            title="互动权限验证",
            source_platform="platform",
            source_visible=True,
            published_at=utc_now(),
            rating="r18",
        )
        session.add(work)
        await session.flush()
        work_id = str(work.id)
    work_url = f"/api/v1/works/{work_id}"
    reader = signed("test-interaction-reader", mode="r18")
    general = signed("test-interaction-reader")
    for path in ("favorite", "like", "follow"):
        url = f"{work_url}/{path}"
        assert (await client.post(url)).status_code == 401
        assert (await client.post(url, headers=general)).status_code == 404
        response = await client.post(url, headers=reader)
        assert response.status_code == 204 and response.content == b""
    response = await client.get("/api/v1/me/follows", headers=reader)
    assert response.headers["X-Content-Mode"] == "r18"
    assert response.json()["items"][0]["work"]["id"] == work_id
    response = await client.get("/api/v1/me/follows", headers=general)
    assert response.json()["items"] == []
    response = await client.post("/api/v1/me/follows/mark-viewed", headers=general)
    assert response.status_code == 204
    for path in ("favorite", "like", "follow"):
        assert (
            await client.delete(f"{work_url}/{path}", headers=reader)
        ).status_code == 204
    assert (await client.get("/api/v1/me/follows", headers=reader)).json()[
        "items"
    ] == []


async def test_work_private_collection_and_trash_lifecycle(platform_http):
    """保存私人书单，按权限删除、恢复、最终清除跨模块投影。"""
    client, sessions = platform_http
    user = f"test-{uuid4().hex}"
    owner = signed(user)
    other = signed("other")
    staff = signed(
        "test-staff",
        permissions=["trash.manage"],
        adminCeiling={"untouchable": [], "higher": []},
    )
    superadmin = signed(
        "test-staff",
        superadmin=True,
        adminCeiling={"untouchable": [], "higher": []},
    )
    title = f"平台事务测试 {user}"
    unauthenticated = await client.post("/api/v1/works", json={"title": title})
    assert unauthenticated.status_code == 401
    created = await client.post(
        "/api/v1/works", headers=owner, json={"title": title, "summary": "独立摘要"}
    )
    assert created.status_code == 201
    work_id = created.json()["id"]
    work = f"/api/v1/works/{work_id}"
    trash = f"/api/v1/admin/trash/works/work:{work_id}"
    policy_url = f"/internal/works/{work_id}/policy"
    policy = {"x-ody-internal-token": "http-test-internal"}
    assert (await client.get(work)).status_code == 404
    assert (await client.get(policy_url, headers=policy)).status_code == 404
    detail = await client.patch(
        work,
        headers=owner,
        json={"description": "作品正文", "layout": {"label": "自定义布局"}},
    )
    assert detail.json()["summary"] == "独立摘要"
    failed = await client.post(
        f"{work}/publish",
        headers=owner,
        json={"acknowledgedTermsVersion": "outdated-terms", "answers": []},
    )
    assert failed.status_code == 422
    async with sessions() as session:
        draft = await session.get(Work, int(work_id))
        assert draft.published_at is None
        assert draft.declarations is None

    published = await client.post(
        f"{work}/publish",
        headers=owner,
        json={"acknowledgedTermsVersion": "http-test-terms", "answers": []},
    )
    assert published.json()["status"] == "published"
    assert published.json()["declarations"] == []
    repeated = await client.post(
        f"{work}/publish",
        headers=owner,
        json={
            "acknowledgedTermsVersion": "http-test-terms",
            "answers": [{"question": "jailbreak", "answer": False}],
        },
    )
    assert repeated.status_code == 409
    async with sessions() as session:
        persisted = await session.get(Work, int(work_id))
        declaration_snapshot = persisted.declarations
        first_published_at = persisted.published_at
        assert declaration_snapshot == {
            "acknowledgedTermsVersion": "http-test-terms",
            "at": timestamp(first_published_at),
            "userId": user,
            "answers": [],
        }
        assert persisted.summary == "独立摘要"
        assert persisted.description == "作品正文"
        assert persisted.layout == {"label": "自定义布局"}

    cleared = await client.patch(work, headers=owner, json={"summary": ""})
    assert cleared.json()["summary"] == "作品正文"
    assert cleared.json()["description"] == "作品正文"
    chat = await client.patch(
        f"{work}/chat/settings", headers=owner, json={"title": "自定义交流区"}
    )
    assert chat.status_code == 200
    assert (await client.get(policy_url, headers=owner)).status_code == 401
    assert (await client.get(policy_url, headers=policy)).json()["authorId"] == user
    search = await client.get("/api/v1/search", params={"q": title})
    assert work_id in [item["id"] for item in search.json()["items"]]
    suggest = await client.get("/api/v1/search/suggest", params={"q": title})
    assert {"id": work_id, "title": title} in suggest.json()["works"]
    blocked = signed("other", blockedUserIds=[user])
    search = await client.get("/api/v1/search", headers=blocked, params={"q": title})
    assert work_id not in [item["id"] for item in search.json()["items"]]

    created_collection = await client.post(
        "/api/v1/collections",
        headers=owner,
        json={"title": "仅自己可见", "isPublic": False},
    )
    assert created_collection.status_code == 201
    collection_id = created_collection.json()["id"]
    collection = f"/api/v1/collections/{collection_id}"
    assert (await client.get(collection, headers=other)).status_code == 404
    assert (await client.get(f"{collection}/items", headers=other)).status_code == 404
    assert (
        await client.post(
            f"{collection}/items",
            headers={"X-Ody-Principal": "invalid"},
            json={"workId": work_id},
        )
    ).status_code == 401
    assert (
        await client.post(
            f"{collection}/items", headers=owner, json={"workId": work_id}
        )
    ).status_code == 201
    assert (await client.get(collection, headers=owner)).json()["itemCount"] == 1
    peer_staff = signed(
        "test-staff",
        permissions=["works.manage_any"],
        adminCeiling={"untouchable": [user], "higher": []},
    )
    assert (
        await client.request(
            "DELETE", work, headers=peer_staff, json={"reason": "站务处理"}
        )
    ).status_code == 403
    assert (await client.delete(work, headers=owner)).status_code == 204
    assert (await client.get(work, headers=owner)).status_code == 404
    async with sessions() as session:
        deleted_work = await session.get(Work, int(work_id))
        assert deleted_work.deleted_user_id == user
    assert (await client.get(collection, headers=owner)).json()["itemCount"] == 0
    assert (
        await client.get(policy_url, headers=policy, params={"includeDeleted": "1"})
    ).json()["deleted"]
    assert (await client.post(f"{work}/restore", headers=owner)).status_code == 403
    assert (await client.post(f"{trash}/restore", headers=staff)).json()["ok"]
    async with sessions() as session:
        restored_work = await session.get(Work, int(work_id))
        assert restored_work.deleted_user_id is None
        assert not restored_work.summary
        assert restored_work.description == "作品正文"
        assert restored_work.published_at == first_published_at
        assert restored_work.declarations == declaration_snapshot
        assert restored_work.chat_title == "自定义交流区"
    assert (await client.get(collection, headers=owner)).json()["itemCount"] == 1
    assert (await client.delete(work, headers=owner)).status_code == 204
    assert (
        await client.request(
            "DELETE", trash, headers=staff, json={"reason": "清除测试数据"}
        )
    ).status_code == 403
    assert (
        await client.request(
            "DELETE", trash, headers=superadmin, json={"reason": "清除测试数据"}
        )
    ).status_code == 204
    assert (
        await client.get(policy_url, headers=policy, params={"includeDeleted": "1"})
    ).status_code == 404

    async with sessions() as session:
        params = {"id": int(work_id)}
        assert (
            await session.execute(
                text('SELECT id FROM "user" WHERE id=:actor'), {"actor": user}
            )
        ).scalar_one() == user
        assert (
            await session.execute(text("SELECT id FROM work WHERE id=:id"), params)
        ).scalar_one_or_none() is None
        purged = (
            await session.execute(
                text(
                    "SELECT payload FROM platform_outbox WHERE work_id=:id AND kind='work.purged'"
                ),
                params,
            )
        ).scalar_one()
    await handle(
        "collections", "ody.events.work.purged", json.dumps(purged), None, sessions
    )
    async with sessions() as session:
        assert (
            await session.execute(
                text("SELECT count(*) FROM booklist_item WHERE work_id=:id"),
                params,
            )
        ).scalar_one() == 0
        assert (
            await session.execute(
                text("SELECT item_count FROM booklist WHERE id=:id"),
                {"id": int(collection_id)},
            )
        ).scalar_one() == 0


async def test_restricted_projection_stays_out_of_public_views(platform_http):
    """全年龄隔离在详情、搜索、书单条目和计数中一致生效。"""
    client, sessions = platform_http
    uid = f"test-{uuid4().hex}"
    title = f"分级测试 {uid}"
    mature = signed(uid, mode="r18")
    works_url = "/api/v1/works"
    created = await client.post(works_url, headers=mature, json={"title": title})
    work_id = created.json()["id"]
    work = f"{works_url}/{work_id}"
    publish = await client.post(
        f"{work}/publish",
        headers=mature,
        json={
            "acknowledgedTermsVersion": "http-test-terms",
            "answers": [{"question": "jailbreak", "answer": True}],
        },
    )
    assert publish.status_code == 200
    answers = publish.json()["declarations"]
    assert [(answer["question"], answer["answer"]) for answer in answers] == [
        ("jailbreak", True)
    ]
    collection = await client.post(
        "/api/v1/collections",
        json={"title": "可见性测试", "isPublic": True},
        headers=mature,
    )
    collection_url = f"/api/v1/collections/{collection.json()['id']}"
    assert (
        await client.post(
            f"{collection_url}/items",
            headers=mature,
            json={"workId": work_id},
        )
    ).status_code == 201
    assert (await client.get(collection_url)).json()["itemCount"] == 0
    assert (await client.get(f"{collection_url}/items")).json()["items"] == []
    assert (await client.get(collection_url, headers=mature)).json()["itemCount"] == 1
    assert (await client.get(work)).status_code == 404
    assert (await client.get(work, headers=mature)).status_code == 200
    assert (await client.get("/api/v1/search", params={"q": title})).json()[
        "items"
    ] == []
    async with sessions.begin() as session:
        restricted_work = await session.get(Work, int(work_id))
        restricted_work.rating = "r18g"
    assert (await client.get(work, headers=mature)).status_code == 404
    r18g = signed(uid, mode="r18", showR18G=True)
    assert (await client.get(work, headers=r18g)).status_code == 200


async def test_collection_uses_shared_tags_and_soft_delete(platform_http):
    """普通标签写入公共绑定，书单软删除同时作用于旧仓库读取。"""
    client, sessions = platform_http
    uid = f"test-{uuid4().hex}"
    owner = signed(uid, mode="r18")
    async with sessions.begin() as session:
        tag = Tag(name=f"书单标签 {uid}", source="custom", category=2)
        session.add(tag)
        await session.flush()
        tag_id = tag.id

    created = await client.post(
        "/api/v1/collections",
        headers=owner,
        json={"title": "共享标签书单", "tags": [str(tag_id), "rating:R18"]},
    )
    assert created.status_code == 201
    booklist_id = int(created.json()["id"])
    collection_url = f"/api/v1/collections/{booklist_id}"
    assert {tag["id"] for tag in created.json()["tags"]} == {
        str(tag_id),
        "rating:R18",
    }
    async with sessions() as session:
        booklist = await session.get(Booklist, booklist_id)
        assert booklist.rating == "r18"
        binding = await session.scalar(
            select(TagBinding).where(
                TagBinding.target_type == "booklist",
                TagBinding.target_id == booklist_id,
            )
        )
        assert binding.tag_id == tag_id
        assert binding.ended_at is None

    tagged = await client.get(
        "/api/v1/me/collections", headers=owner, params={"tags": str(tag_id)}
    )
    assert [item["id"] for item in tagged.json()["items"]] == [str(booklist_id)]
    cleared = await client.patch(collection_url, headers=owner, json={"tags": []})
    assert [tag["id"] for tag in cleared.json()["tags"]] == ["rating:全年龄"]
    async with sessions() as session:
        booklist = await BooklistRepository(session).get_booklist(booklist_id)
        assert booklist is not None
        assert booklist.rating == "general"
    tagged = await client.get(
        "/api/v1/me/collections", headers=owner, params={"tags": str(tag_id)}
    )
    assert tagged.json()["items"] == []
    deleted = await client.request(
        "DELETE", collection_url, headers=owner, json={"reason": "作者删除"}
    )
    assert deleted.status_code == 204
    assert (await client.get(collection_url, headers=owner)).status_code == 404
    async with sessions() as session:
        assert await BooklistRepository(session).get_booklist(booklist_id) is None
        booklist = await session.get(Booklist, booklist_id)
        assert booklist.deleted_at is not None
        assert booklist.deleted_user_id == uid
        assert booklist.deletion_reason == "作者删除"
        assert (await session.get(TagBinding, binding.id)).ended_at is not None


async def test_discord_unclaimed_authors_keep_distinct_source_references(platform_http):
    """DC 保留原作者快照与回链，未绑定作者不可误作平台账号。"""
    client, sessions = platform_http
    base_id = 10**15 + uuid4().int % 10**14
    thread_ids = []
    async with sessions.begin() as session:
        for i in range(2):
            discord_author = base_id + i
            thread_id = base_id + 100 + i
            thread_ids.append(thread_id)
            session.add(
                Author(
                    id=discord_author, name=f"source-{i}", display_name=f"DC 作者 {i}"
                )
            )
            session.add(
                Thread(
                    thread_id=thread_id,
                    author_id=discord_author,
                    guild_id=901,
                    channel_id=902,
                    title=f"DC 来源作品 {i}",
                    thumbnail_urls=["https://example.org/original.png"],
                )
            )
    author_refs = []
    for i, thread_id in enumerate(thread_ids):
        resolved = await client.get(f"/api/v1/works/by-legacy/discord/{thread_id}")
        assert resolved.status_code == 200
        work_id = resolved.json()["workId"]
        response = await client.get(f"/api/v1/works/{work_id}")
        assert response.status_code == 200
        work = response.json()
        assert work["status"] == "unclaimed"
        assert work["author"]["id"].startswith("legacy:discord:")
        assert work["author"]["discordLinked"] is False
        assert work["origin"]["legacyAuthorName"] == f"DC 作者 {i}"
        assert work["origin"]["url"] == f"https://discord.com/channels/901/{thread_id}"
        assert work["cover"] is None and work["gallery"] == []
        author_refs.append(work["author"]["id"])
    assert len(set(author_refs)) == 2


async def test_rankings_use_distinct_growth_windows_and_hall_age(platform_http):
    """日、周、月榜采用各自 UTC 窗口，殿堂要求发布至少 90 天。"""
    client, sessions = platform_http
    author = f"test-ranking-{uuid4().hex}"
    now = utc_now()
    ids = []
    async with sessions.begin() as session:
        for i, (age_days, growth_days, likes, delta) in enumerate(
            [
                (3, 0, 400, 8),
                (10, 4, 300, 20),
                (100, 20, 250, 60),
            ]
        ):
            published = now - timedelta(days=age_days)
            work = Work(
                title=f"增长榜 {i}",
                author_id=author,
                source_platform="platform",
                created_at=published,
                published_at=published,
            )
            session.add(work)
            await session.flush()
            ids.append(str(work.id))
            session.add(WorkStatistics(work_id=work.id, reaction_count=likes))
            session.add(
                WorkGrowthDaily(
                    work_id=work.id,
                    metric="reaction",
                    day=now.date() - timedelta(days=growth_days),
                    delta=delta,
                )
            )
        session.add(
            WorkGrowthDaily(
                work_id=int(ids[0]),
                metric="reaction",
                day=now.date() + timedelta(days=1),
                delta=1000,
            )
        )
    for order, first_id in (
        ("hot_day", ids[0]),
        ("hot_week", ids[1]),
        ("hot_month", ids[2]),
    ):
        response = await client.get(
            "/api/v1/search", params={"sort": order, "authors": author}
        )
        assert response.status_code == 200
        assert response.json()["items"][0]["id"] == first_id
    default = await client.get("/api/v1/works", params={"authors": author})
    assert default.json()["items"][0]["id"] == ids[1]
    hall = await client.get("/api/v1/works", params={"sort": "hall", "authors": author})
    assert [item["id"] for item in hall.json()["items"]] == [ids[2]]
    for missing in ("rising", "rookie"):
        response = await client.get("/api/v1/works", params={"sort": missing})
        assert response.status_code == 501
        assert response.json()["error"]["code"] == "not_implemented"
