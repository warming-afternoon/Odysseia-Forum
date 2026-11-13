"""按作品真实主来源安全补抓失效图片。"""

import asyncio
import os
import uuid

import httpx
from fastapi import HTTPException
from sqlalchemy import String, cast, or_, select, text
from sqlalchemy.exc import SQLAlchemyError

from dto.work.work_command_qo import WorkCommandQo
from dto.work.work_image_snapshot_dto import WorkImageSnapshotDTO
from identity.auth_runtime import get_auth_service
from identity.integration_rpc_error import IntegrationRpcError
from models.thread import Thread
from models.work import Work
from shared.image_url_utils import extract_message_image_urls
from shared.redis_client import RedisManager
from work.work_repository import WorkRepository


class WorkImageService:
    """批量解析来源，释放事务后请求平台，再核对快照写回。"""

    def __init__(self, sessions, *, redis=None, rpc=None, client=None):
        """允许隔离验证注入网络和限流依赖。"""
        self.sessions = sessions
        self.redis = redis
        self.rpc = rpc
        self.client = client

    async def refresh(self, command: WorkCommandQo):
        """只处理当前成员可见作品，不接受客户端提供的源站地址。"""
        items = command.values.get("items")
        if set(command.values) != {"items"} or not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise HTTPException(422, "每次只能补抓1至20个作品")
        targets = []
        for item in items:
            if not isinstance(item, dict) or len(item) != 1:
                raise HTTPException(422, "请明确提供作品编号或旧Discord帖子编号")
            key = next(iter(item))
            value = item[key]
            if key not in {"work_id", "discord_thread_id"} or isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isascii() or not str(value).isdigit() or len(str(value)) > 19 or not 0 < int(value) < 2**63:
                raise HTTPException(422, "无效图片补抓编号")
            target = (key, int(value))
            if target not in targets:
                targets.append(target)
        principal = command.actor
        filters, _, _ = WorkRepository.visibility_filters(principal)
        async with self.sessions() as session:
            # 同批加载作品及其唯一真实主帖，旧入口只解析Discord来源。
            rows = (await session.execute(
                select(Work, Thread).join(Thread, (Thread.work_id == Work.id) & (Thread.role == "main") & (Thread.platform == Work.source_platform) & (cast(Thread.thread_id, String) == Work.source_thread_id) & (cast(Thread.guild_id, String) == Work.source_guild_id) & (cast(Thread.channel_id, String) == Work.source_channel_id))
                .where(*filters, Thread.deleted_at.is_(None), Thread.show_flag.is_(True), or_(
                    Work.id.in_([value for key, value in targets if key == "work_id"]),
                    (Thread.platform == "discord") & Thread.thread_id.in_([value for key, value in targets if key == "discord_thread_id"]),
                ))
            )).all()
            snapshots = {}
            protected = set()
            for work, thread in rows:
                snapshot = WorkImageSnapshotDTO(work.id, thread.id, work.source_platform, work.source_guild_id, work.source_channel_id, work.source_thread_id, thread.source_version, tuple(work.images), tuple(thread.thumbnail_urls or []))
                snapshots[("work_id", work.id)] = snapshot
                if thread.platform == "discord":
                    snapshots[("discord_thread_id", thread.thread_id)] = snapshot
                if "images" in (work.metadata_overrides or []):
                    protected.add(work.id)
        results = []
        processed = {}
        semaphore = asyncio.Semaphore(4)
        redis = self.redis if self.redis is not None else RedisManager.get_client()
        async with httpx.AsyncClient(timeout=8.0) as default_client:
            client = self.client or default_client
            async def fetch(snapshot):
                async with semaphore:
                    try:
                        return await self.refresh_one(snapshot, principal, client)
                    except SQLAlchemyError:
                        # 单项事务已由会话上下文回滚，不暴露数据库或连接细节。
                        return {"error": "persist_failed"}

            for key, value in targets:
                snapshot = snapshots.get((key, value))
                result = {key: str(value), "thumbnail_urls": [], "updated": False}
                if snapshot is None:
                    results.append({**result, "error": "not_found"})
                    continue
                result["work_id"] = str(snapshot.work_id)
                if snapshot.work_id in processed:
                    results.append((result, snapshot.work_id))
                    continue
                if snapshot.work_id in protected:
                    outcome = {"error": "author_override"}
                else:
                    try:
                        # 跨进程短冷却同时限制成功、空结果和失败的重复源站请求。
                        allowed = await redis.set(f"work:image-refresh:{snapshot.work_id}", "1", nx=True, ex=60)
                    except Exception:
                        allowed = False
                    if not allowed:
                        outcome = {"error": "retry_later"}
                    else:
                        outcome = asyncio.create_task(fetch(snapshot))
                processed[snapshot.work_id] = outcome
                results.append((result, snapshot.work_id))
            tasks = [outcome for outcome in processed.values() if isinstance(outcome, asyncio.Task)]
            if tasks:
                await asyncio.gather(*tasks)
            results = [
                item if isinstance(item, dict) else {
                    **item[0],
                    **(processed[item[1]].result() if isinstance(processed[item[1]], asyncio.Task) else processed[item[1]]),
                }
                for item in results
            ]
        return {"results": results}

    async def refresh_one(self, snapshot, principal, client):
        """使用固定平台客户端读取首消息，不在失败时清空图片。"""
        try:
            if snapshot.platform == "discord":
                token = os.environ.get("BOT_TOKEN", "").strip()
                if not token:
                    return {"error": "source_unavailable"}
                response = await client.get(
                    f"https://discord.com/api/v10/channels/{snapshot.thread_id}/messages/{snapshot.thread_id}",
                    headers={"Authorization": f"Bot {token}"},
                )
                if response.status_code != 200:
                    return {"error": "source_unavailable"}
                message = response.json()
            elif snapshot.platform == "fluxer":
                rpc = self.rpc or get_auth_service().rpc
                response = await asyncio.wait_for(rpc(
                    "rpc.content.v1.thread.read_service",
                    operation_id="forum_image_refresh_" + uuid.uuid4().hex,
                    actor_user_id=principal.id,
                    guild_id=snapshot.guild_id,
                    thread_id=snapshot.thread_id,
                ), timeout=8.0)
                if not isinstance(response, dict):
                    return {"error": "source_unavailable"}
                if str(response.get("guild_id")) != snapshot.guild_id or str(response.get("thread_id")) != snapshot.thread_id or str(response.get("channel_id")) != snapshot.channel_id:
                    return {"error": "source_changed"}
                message = response.get("message")
            else:
                return {"error": "unsupported_source"}
            if not isinstance(message, dict):
                return {"error": "source_unavailable"}
            if str(message.get("id")) != snapshot.thread_id or str(message.get("channel_id")) != snapshot.thread_id:
                return {"error": "source_changed"}
            urls = extract_message_image_urls(attachments=message.get("attachments") or [], embeds=message.get("embeds") or [], content=message.get("content") or "")
            if not urls:
                return {"error": "no_images"}
        except (httpx.HTTPError, ValueError, TypeError, IntegrationRpcError, TimeoutError):
            return {"error": "source_unavailable"}
        async with self.sessions() as session:
            # 复用作品写入口的事务锁，再锁行，避免与事件投影交叉死锁。
            await session.execute(text("SELECT pg_advisory_xact_lock(:id)"), {"id": snapshot.work_id})
            filters, _, _ = WorkRepository.visibility_filters(principal)
            work = (await session.execute(select(Work).where(Work.id == snapshot.work_id, *filters).with_for_update())).scalar_one_or_none()
            if work is None:
                return {"error": "not_found"}
            thread = (await session.execute(select(Thread).where(Thread.id == snapshot.thread_row_id).with_for_update())).scalar_one_or_none()
            if thread is None or thread.deleted_at or not thread.show_flag or thread.work_id != work.id or thread.role != "main" or thread.platform != snapshot.platform or str(thread.guild_id) != snapshot.guild_id or str(thread.channel_id) != snapshot.channel_id or str(thread.thread_id) != snapshot.thread_id or thread.source_version != snapshot.version or tuple(thread.thumbnail_urls or []) != snapshot.thread_images or (work.source_platform, work.source_guild_id, work.source_channel_id, work.source_thread_id) != (snapshot.platform, snapshot.guild_id, snapshot.channel_id, snapshot.thread_id) or tuple(work.images) != snapshot.images or "images" in (work.metadata_overrides or []):
                return {"error": "source_changed"}
            thread.thumbnail_urls = urls
            work.images = urls
            session.add(thread)
            session.add(work)
            await session.commit()
            return {"thumbnail_urls": urls, "updated": True}
