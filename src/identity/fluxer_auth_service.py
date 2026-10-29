"""持久 NATS 认证连接与有界、可失效的哈希会话缓存。"""

import asyncio
import hashlib
import json
import math
import os
import time
import uuid

from fastapi import HTTPException


class FluxerAuthService:
    """只信任权威 RPC 返回的主体；缓存故障和依赖故障均返回 503。"""

    PREFIX = "forum:fluxer:auth:v1"

    def __init__(self, redis, connector=None):
        self.redis = redis
        self.connector = connector
        self.connection = None
        self.ready = False
        self.start_lock = asyncio.Lock()
        self.misses = asyncio.Semaphore(64)

    async def start(self):
        """连接、订阅失效事件并轮换缓存命名空间后才开放认证。"""
        async with self.start_lock:
            if self.connection is not None and self.ready:
                return
            if self.connection is not None:
                raise HTTPException(503, "Fluxer 认证连接恢复中")
            try:
                connect = self.connector
                if connect is None:
                    import nats

                    connect = nats.connect
                self.connection = await connect(
                    servers=os.environ.get(
                        "INTEGRATION_NATS_URL", "nats://127.0.0.1:4222"
                    ).split(","),
                    user=os.environ.get("INTEGRATION_NATS_USER"),
                    password=os.environ.get("INTEGRATION_NATS_PASSWORD"),
                    inbox_prefix=b"_INBOX.forum",
                    connect_timeout=3,
                    max_reconnect_attempts=-1,
                    disconnected_cb=self.disconnected,
                    reconnected_cb=self.reconnected,
                )
                for subject in (
                    "events.auth.v1.session_invalidated",
                    "events.auth.v1.user_invalidated",
                    "events.identity.v1.binding_changed",
                ):
                    await self.connection.subscribe(subject, cb=self.invalidated)
                await self.connection.subscribe(
                    "rpc.forum.v1.service_info", cb=self.service_info
                )
                await self.connection.flush(timeout=3)
                await self.redis.incr(self.PREFIX + ":generation")
                self.ready = True
            except Exception as exc:
                self.ready = False
                if self.connection:
                    await self.connection.close()
                    self.connection = None
                raise HTTPException(503, "Fluxer 认证服务暂不可用") from exc

    async def disconnected(self):
        """连接断开期间不使用可能漏掉失效事件的缓存。"""
        self.ready = False

    async def reconnected(self):
        """重连后整个缓存命名空间失效，阻止断线期间的撤销继续生效。"""
        try:
            await self.redis.incr(self.PREFIX + ":generation")
            self.ready = True
        except Exception:
            self.ready = False

    async def invalidated(self, message):
        """全局代次失效同时阻断在途旧请求回填，事件无需扫描 token。"""
        try:
            await self.redis.incr(self.PREFIX + ":generation")
            if message and message.subject == "events.identity.v1.binding_changed":
                from sqlalchemy.dialects.postgresql import insert
                from models.discord_identity import DiscordIdentity
                from shared.database import AsyncSessionFactory

                event = json.loads(message.data)
                async with AsyncSessionFactory() as session:
                    statement = insert(DiscordIdentity).values(
                        discord_user_id=str(event["discord_user_id"]),
                        user_id=str(event["user_id"]),
                        version=int(event["version"]),
                    )
                    await session.execute(
                        statement.on_conflict_do_update(
                            index_elements=[DiscordIdentity.discord_user_id],
                            set_={
                                "user_id": statement.excluded.user_id,
                                "version": statement.excluded.version,
                            },
                            where=DiscordIdentity.version < statement.excluded.version,
                        )
                    )
                    await session.commit()
        except Exception:
            self.ready = False

    async def service_info(self, message):
        """仅对版本化请求提供认证与身份投影状态。"""
        try:
            request = json.loads(message.data)
            valid = request.get("version") == 1 and isinstance(
                request.get("request_id"), str
            )
            reply = (
                {
                    "ok": True,
                    "data": {"service": "forum", "version": 1, "ready": self.ready},
                }
                if valid
                else {
                    "ok": False,
                    "error": {
                        "code": "INVALID_REQUEST",
                        "message": "Invalid envelope",
                        "retryable": False,
                    },
                }
            )
            await message.respond(json.dumps(reply).encode())
        except Exception:
            return

    async def rpc(self, subject, **fields):
        """以固定超时执行服务请求，不记录任何原始凭证。"""
        await self.start()
        try:
            message = await self.connection.request(
                subject,
                json.dumps(
                    {"version": 1, "request_id": str(uuid.uuid4()), **fields}
                ).encode(),
                timeout=5,
            )
            reply = json.loads(message.data)
            if reply.get("ok") is True and isinstance(reply.get("data"), dict):
                return reply["data"]
            error = reply.get("error", {})
            if (
                subject == "rpc.auth.v1.session.resolve"
                and error.get("code") in {"INVALID_TOKEN", "ACCOUNT_UNAVAILABLE"}
                and error.get("retryable") is False
            ):
                return None
            if (
                subject == "rpc.auth.v1.external.prove"
                and error.get("code") == "PROOF_NOT_AVAILABLE"
            ):
                return None
            raise HTTPException(503, "Fluxer 认证服务暂不可用")
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "Fluxer 认证服务暂不可用") from exc

    async def resolve(self, token):
        """正缓存最长从请求开始计时一小时，明确无效只缓存五秒。"""
        started = time.time()
        if (
            not isinstance(token, str)
            or not token.startswith("flx_")
            or len(token) > 4096
        ):
            return None
        await self.start()
        digest = hashlib.sha256(token.encode()).hexdigest()
        generation_key = self.PREFIX + ":generation"
        try:
            generation = str(await self.redis.get(generation_key) or "0")
            key = f"{self.PREFIX}:{generation}:{digest}"
            cached = await self.redis.eval(
                """if (redis.call('GET',KEYS[1]) or '0') ~= ARGV[1] then return nil end
                return redis.call('GET',KEYS[2])""",
                2,
                generation_key,
                key,
                generation,
            )
            if cached:
                snapshot = json.loads(cached)
                if self.ready and snapshot["expires_at"] > started:
                    return snapshot["user"]
            # 等待队列有时间边界，攻击者不能堆积无限外部请求。
            try:
                await asyncio.wait_for(self.misses.acquire(), timeout=0.1)
            except TimeoutError as exc:
                raise HTTPException(503, "Fluxer 认证请求繁忙") from exc
            try:
                user = await self.rpc("rpc.auth.v1.session.resolve", token=token)
            finally:
                self.misses.release()
            if user is not None and (
                not isinstance(user.get("user_id"), str)
                or not isinstance(user.get("session_id_hash"), str)
                or not user.get("session_id_hash")
                or not user.get("user_id", "").isdecimal()
                or user.get("user_id") == "0"
                or not isinstance(user.get("username"), str)
                or (
                    user.get("discord_user_id") is not None
                    and (
                        not isinstance(user["discord_user_id"], str)
                        or not user["discord_user_id"].isdecimal()
                    )
                )
            ):
                raise HTTPException(503, "Fluxer 返回了无效身份")
            expires_at = started + (3600 if user else 5)
            ttl = math.floor(expires_at - time.time())
            if not self.ready or ttl < 1:
                raise HTTPException(503, "Fluxer 认证结果已过期")
            # 检查代次与写入在 Redis 中原子完成，撤销事件不能被旧 RPC 覆盖。
            accepted = await self.redis.eval(
                """if (redis.call('GET',KEYS[1]) or '0') ~= ARGV[1] then return 0 end
                redis.call('SET',KEYS[2],ARGV[2],'EX',ARGV[3]); return 1""",
                2,
                generation_key,
                key,
                generation,
                json.dumps({"user": user, "expires_at": expires_at}),
                ttl,
            )
            if not accepted:
                raise HTTPException(503, "认证状态已变化，请重试")
            return user
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(503, "认证缓存暂不可用") from exc

    async def close(self):
        """关闭持久连接，进程退出时不留下订阅。"""
        self.ready = False
        if self.connection:
            await self.connection.close()
            self.connection = None
