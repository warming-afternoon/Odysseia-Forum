"""在初次导入或停消费超过事件保留期后，从 Core 重建 Discord 绑定。"""

import asyncio
import os

import httpx
from sqlalchemy import text

from platform_bus.bindings import apply_bindings
from platform_bus.worker import current_bindings
from shared.database import AsyncSessionFactory


async def reconcile(client, sessions=AsyncSessionFactory):
    """先刷新已知主体（包括解绑），再按来源 ID 查找缺少或变化的绑定。"""
    after_user = ""
    while True:
        async with sessions() as session:
            users = list(
                (
                    await session.execute(
                        text("""
                SELECT user_id FROM platform_binding_version WHERE user_id>:after
                ORDER BY user_id LIMIT 256"""),
                        {"after": after_user},
                    )
                ).scalars()
            )
        for uid in users:
            snapshot = await current_bindings(client, uid)
            async with sessions.begin() as session:
                await apply_bindings(session, snapshot)
        if len(users) < 256:
            break
        after_user = users[-1]

    after = 0
    while True:
        async with sessions() as session:
            source_ids = list(
                (
                    await session.execute(
                        text("""
                SELECT source FROM (
                    (SELECT id AS source FROM author WHERE id>:after ORDER BY id LIMIT 256)
                    UNION (SELECT DISTINCT author_id FROM thread WHERE platform='discord' AND author_id>:after ORDER BY author_id LIMIT 256)
                    UNION (SELECT DISTINCT owner_id FROM booklist WHERE owner_id>:after ORDER BY owner_id LIMIT 256)
                    UNION (SELECT DISTINCT user_id FROM thread_follow WHERE user_id>:after ORDER BY user_id LIMIT 256)
                    UNION (SELECT DISTINCT user_id FROM user_collection WHERE user_id>:after ORDER BY user_id LIMIT 256)
                    UNION (SELECT DISTINCT user_id FROM author_follow WHERE user_id>:after ORDER BY user_id LIMIT 256)
                    UNION (SELECT DISTINCT author_id FROM author_follow WHERE author_id>:after ORDER BY author_id LIMIT 256)
                ) AS known ORDER BY source LIMIT 256"""),
                        {"after": after},
                    )
                ).scalars()
            )
        seen = set()
        for discord in source_ids:
            lookup = await client.get(
                f"/internal/users/by-discord/{discord}",
                headers={"x-ody-internal-token": os.environ["FORUM_DC_LOOKUP_TOKEN"]},
            )
            if lookup.status_code == 404:
                continue
            lookup.raise_for_status()
            uid = lookup.json()["id"]
            if uid in seen:
                continue
            snapshot = await current_bindings(client, uid)
            async with sessions.begin() as session:
                await apply_bindings(session, snapshot)
            seen.add(uid)
        if len(source_ids) < 256:
            break
        after = source_ids[-1]


async def main():
    """使用部署配置的网关和独立 Forum 数据库。"""
    async with httpx.AsyncClient(
        base_url=os.environ["FORUM_CORE_URL"], timeout=8
    ) as client:
        await reconcile(client)


if __name__ == "__main__":
    asyncio.run(main())
