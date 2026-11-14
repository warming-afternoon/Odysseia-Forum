"""仅为作品公告核验作者及其直接协作者权限。"""
import os
from urllib.parse import urlsplit

import httpx
from fastapi import HTTPException
from sqlalchemy import select

from models.discord_identity import DiscordIdentity
from work.work_service import WorkService


def _authority_config():
    url = os.environ.get("FORUM_COLLABORATOR_AUTH_URL", "")
    secret = os.environ.get("FORUM_COLLABORATOR_AUTH_SECRET", "")
    parsed = urlsplit(url)
    local_http = parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "::1"}
    if (
        not url or len(secret) < 32 or parsed.username or parsed.password
        or parsed.query or parsed.fragment or not parsed.hostname
        or (parsed.scheme != "https" and not local_http)
    ):
        raise HTTPException(503, "协作者授权服务尚未安全配置")
    return url.rstrip("/") + "/internal/v1/collaborators/check", secret


async def require_announcement_manager(session, work_id, principal):
    """不扩大作品的其他管理权限；协作者使用 protect 的实时直接授权。"""
    work = await WorkService(session).visible(work_id, principal)
    if work.author_id == principal["id"] or principal["is_admin"]:
        return work
    if not work.author_id or not principal.get("discord_user_id"):
        raise HTTPException(403, "需要作品作者或其协作者权限")
    # 固定双方当前绑定，避免查询 protect 期间身份映射被并发更换。
    bindings = (
        await session.execute(
            select(DiscordIdentity.user_id, DiscordIdentity.discord_user_id)
            .where(
                DiscordIdentity.user_id.in_([work.author_id, principal["id"]]),
                DiscordIdentity.is_current.is_(True),
            )
            .order_by(DiscordIdentity.discord_user_id)
            .with_for_update(read=True)
        )
    ).all()
    current = {row.user_id: row.discord_user_id for row in bindings}
    author_discord = current.get(work.author_id)
    if author_discord is None or current.get(principal["id"]) != str(principal["discord_user_id"]):
        raise HTTPException(403, "需要作品作者或其协作者权限")
    url, secret = _authority_config()
    try:
        async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as client:
            response = await client.post(
                url,
                json={"author_id": str(author_discord), "collaborator_id": str(principal["discord_user_id"])},
                headers={"Authorization": "Bearer " + secret},
            )
            response.raise_for_status()
            result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(503, "协作者授权服务暂不可用") from exc
    if not isinstance(result, dict) or type(result.get("allowed")) is not bool:
        raise HTTPException(503, "协作者授权服务响应无效")
    if not result["allowed"]:
        raise HTTPException(403, "需要作品作者或其协作者权限")
    return work
