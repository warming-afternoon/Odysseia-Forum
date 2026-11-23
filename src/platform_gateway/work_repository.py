"""平台作品读取与结构化响应。"""

from collections import defaultdict
from hashlib import sha256

from sqlalchemy import select

from models.author import Author
from models.booklist import Booklist
from models.booklist_item import BooklistItem
from models.tag import Tag
from models.thread import Thread
from models.thread_follow import ThreadFollow
from models.work import Work
from models.work_like import WorkLike
from platform_gateway.http import timestamp
from platform_gateway.profile_client import core_profiles
from platform_gateway.policy import RATING_NAMES, visible_booklist_conditions
from work.work_ack_repository import work_ack_states


CATEGORY_GROUPS = {
    1: "fetish",
    2: "ip",
    3: "character",
    4: "trait",
    5: "plot",
    6: "setting",
    7: "gameplay",
}


def public_user(user_id, profile, created_at):
    """仅输出公开资料；缺少同步资料时显示占位作者。"""
    return {
        "id": user_id or "unknown",
        "username": profile.username if profile else "",
        "displayName": (profile.display_name or profile.username)
        if profile
        else "未知用户",
        "avatar": None,
        "decorations": {"badges": []},
        "createdAt": timestamp(profile.updated_at if profile else created_at),
        "discordLinked": False,
        "isNewcomer": False,
    }


def rating_tag(rating):
    """返回作品和书单使用的标准分级标签。"""
    name = RATING_NAMES[rating]
    return {
        "id": f"rating:{name}",
        "name": name,
        "group": "rating",
        "addedBy": None,
        "restricts": rating != "general",
        "official": True,
    }


def tag_ref(tag, category, restricted):
    """把 Forum 标签分类映射到平台 TagRef。"""
    return {
        **tag,
        "group": CATEGORY_GROUPS.get(category, "uncategorized"),
        "restricts": restricted,
        "addedBy": None,
        "official": True,
    }


def work_query():
    """查询作品正本。"""
    return select(Work)


class WorkRepository:
    """批量读取平台作品的公开投影。"""

    def __init__(self, session):
        """复用请求的数据库会话。"""
        self.session = session

    async def project(self, works, principal):
        """复用 Forum 批量作品投影并转换平台响应。"""
        from work.work_service import WorkService

        if not works:
            return []
        projected = await WorkService(self.session).project(works, platform_likes=True)
        work_ids = [work.id for work in works]
        collections = defaultdict(list)
        favorites = set()
        likes = set()
        follows = set()
        if principal.userId:
            membership = (
                select(BooklistItem.work_id, Booklist.id, Booklist.is_default)
                .join(Booklist, Booklist.id == BooklistItem.booklist_id)
                .where(
                    Booklist.fluxer_owner_id == principal.userId,
                    *visible_booklist_conditions(principal),
                    BooklistItem.work_id.in_(work_ids),
                )
            )
            for work_id, booklist_id, is_default in await self.session.execute(
                membership
            ):
                collections[work_id].append(str(booklist_id))
                if is_default:
                    favorites.add(work_id)
            likes = set(
                await self.session.scalars(
                    select(WorkLike.work_id).where(
                        WorkLike.user_id == principal.userId,
                        WorkLike.work_id.in_(work_ids),
                    )
                )
            )
            follows = set(
                await self.session.scalars(
                    select(ThreadFollow.work_id).where(
                        ThreadFollow.fluxer_user_id == principal.userId,
                        ThreadFollow.work_id.in_(work_ids),
                        ThreadFollow.active_flag.is_(True),
                    )
                )
            )
        ack_states = await work_ack_states(self.session, work_ids, principal.userId)
        tag_ids = {int(tag["id"]) for row in projected for tag in row["tags"]}
        classes = {}
        if tag_ids:
            classes = {
                row.id: (row.category, row.is_abyss)
                for row in await self.session.execute(
                    select(Tag.id, Tag.category, Tag.is_abyss).where(
                        Tag.id.in_(tag_ids)
                    )
                )
            }
        discord_ids = [work.id for work in works if work.source_platform == "discord"]
        origins = {}
        if discord_ids:
            query = (
                select(
                    Thread.work_id,
                    Thread.reaction_count,
                    Thread.author_id,
                    Author.display_name,
                )
                .outerjoin(Author, Author.id == Thread.author_id)
                .where(
                    Thread.work_id.in_(discord_ids),
                    Thread.platform == "discord",
                    Thread.role == "main",
                )
            )
            origins = {
                wid: (reactions, source_author, name)
                for wid, reactions, source_author, name in await self.session.execute(
                    query
                )
            }
        profiles = await core_profiles(work.author_id for work in works)
        result = []
        for work, view in zip(works, projected, strict=True):
            owner = principal.userId is not None and work.author_id == principal.userId
            staff = "works.manage_any" in principal.permissions
            author = view["author"]
            rating = work.rating or ("r18" if work.is_nsfw else "general")
            tags = [
                tag_ref(
                    {"id": tag["id"], "name": tag["name"]},
                    *classes.get(int(tag["id"]), (None, False)),
                )
                for tag in view["tags"]
            ]
            reactions, source_author, name = origins.get(work.id, (0, None, None))
            origin = None
            if work.source_platform == "discord" and work.source_thread_id:
                origin = {
                    "kind": "discord",
                    "url": work.main_ref
                    or f"https://discord.com/channels/{work.source_guild_id}/{work.source_thread_id}",
                    "legacyThreadId": work.source_thread_id,
                    "legacyGuild": "main",
                    "legacyChannel": work.source_channel_id,
                    "legacyAuthorName": name or "",
                    "legacyReactions": reactions,
                    "archived": False,
                    "archivePostCount": 0,
                }
            # 未认领作者仅有来源占位编号，不会参与平台账户权限判断。
            author_ref = work.author_id
            if not author_ref:
                if source_author is not None:
                    digest = sha256(str(source_author).encode()).hexdigest()[:24]
                    author_ref = "legacy:discord:" + digest
                else:
                    author_ref = f"legacy:work:{work.id}"
            status = (
                "draft"
                if work.source_platform == "platform" and work.published_at is None
                else "published"
            )
            if origin and work.author_id is None:
                status = "unclaimed"
            result.append(
                {
                    "id": str(work.id),
                    "title": work.title,
                    "chatTitle": work.chat_title,
                    "summary": work.summary
                    if work.summary is not None
                    else (work.description or "")[:200],
                    "description": work.description or "",
                    "author": {
                        "id": author_ref,
                        "username": author["username"] if author else "",
                        "displayName": (author["display_name"] or author["username"])
                        if author
                        else name or "未知用户",
                        "avatar": None,
                        "decorations": {"badges": []},
                        "createdAt": timestamp(work.created_at),
                        "discordLinked": bool(origin and work.author_id),
                        "isNewcomer": False,
                        **profiles.get(work.author_id, {}),
                    },
                    "status": status,
                    "rating": rating,
                    "visibility": "restricted"
                    if work.is_nsfw
                    or work.is_abyss
                    or rating != "general"
                    or any(tag["restricts"] for tag in tags)
                    else "public",
                    "declarations": (work.declarations or {}).get("answers", []),
                    "ratingLock": None,
                    "moderationState": "ok",
                    "tags": [rating_tag(rating), *tags],
                    "cover": None,
                    "gallery": [],
                    "latestVersion": None,
                    "stats": {
                        "favorites": view["statistics"]["collection_count"],
                        "likes": view["statistics"]["platform_like_count"]
                        + (
                            view["statistics"]["reaction_count"]
                            if work.source_platform == "discord"
                            else 0
                        ),
                        "downloads": 0,
                        "chatMessages": view["statistics"]["reply_count"],
                    },
                    "layout": work.layout,
                    "origin": origin,
                    "createdAt": timestamp(work.created_at),
                    "updatedAt": timestamp(
                        work.updated_at or work.last_active_at or work.created_at
                    ),
                    "viewer": {
                        "favorited": work.id in favorites,
                        "liked": work.id in likes,
                        "inCollections": collections[work.id],
                        "followed": work.id in follows,
                        "role": "author" if owner else "none",
                        "canEdit": owner or staff,
                        "canModerate": owner or staff,
                        "canDelete": owner
                        or principal.may_moderate(
                            work.author_id, {"works.manage_any", "moderation.content"}
                        ),
                        "needsAck": {
                            "announcement": False,
                            "rules": False,
                            "version": False,
                            **ack_states.get(work.id, {}),
                        },
                    },
                }
            )
        return result
