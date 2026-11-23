"""从账号模块批量解析当前可见内容的作者公开资料。"""

import logging
import os

import httpx
from sqlalchemy.dialects.postgresql import insert
from models.user import User


log = logging.getLogger(__name__)
PUBLIC_FIELDS = (
    "id",
    "uid",
    "username",
    "displayName",
    "avatar",
    "decorations",
    "createdAt",
    "discordLinked",
    "isNewcomer",
)


async def core_profiles(ids):
    """仅发送本页已通过可见性检查的作者 ID。"""
    ids = sorted({user_id for user_id in ids if user_id})
    base_url = os.getenv("FORUM_CORE_URL", "").rstrip("/")
    token = os.getenv("FORUM_CORE_PROFILE_TOKEN", "")
    if not ids or not base_url or not token:
        return {}
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.post(
                f"{base_url}/internal/users/batch",
                headers={"x-ody-internal-token": token},
                json={"ids": ids},
            )
            response.raise_for_status()
            rows = response.json()["items"]
        if not isinstance(rows, list):
            return {}
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        log.warning("平台账号公开资料暂不可用")
        return {}
    profiles = {}
    for row in rows:
        if not isinstance(row, dict) or row.get("id") not in ids:
            continue
        user_id = row["id"]
        if row.get("deleted"):
            profiles[user_id] = {
                "id": user_id,
                "username": "deleted",
                "displayName": "已注销用户",
                "avatar": None,
                "decorations": {"badges": []},
                "discordLinked": False,
                "isNewcomer": False,
            }
            continue
        profile = {key: row[key] for key in PUBLIC_FIELDS if key in row}
        if isinstance(row.get("decoration"), dict):
            profile["decorations"] = {"badges": [], **row["decoration"]}
        if row.get("avatarUrl"):
            profile["avatar"] = {
                "id": "avatar_" + user_id,
                "kind": "image",
                "mime": "image/png",
                "size": 0,
                "name": "avatar",
                "url": row["avatarUrl"],
                "placeholder": False,
                "thumbUrl": row["avatarUrl"],
                "rating": "general",
                "reviewState": "auto",
                "state": "ready",
            }
        profiles[user_id] = profile
    return profiles


async def ensure_local_user(session, user_id):
    """为新平台写操作建立同一用户 ID 的本地投影。"""
    existing = await session.get(User, user_id)
    if existing is not None:
        return existing
    profiles = await core_profiles([user_id])
    profile = profiles.get(user_id, {})
    statement = insert(User).values(
        id=user_id,
        username=profile.get("username") or user_id,
        display_name=profile.get("displayName"),
    )
    await session.execute(statement.on_conflict_do_nothing(index_elements=[User.id]))
    return await session.get(User, user_id)
