"""通知网关短期一次性票据；不得包含或记录登录令牌。"""
import hashlib
import json
import re
import secrets

from fastapi import HTTPException


class NotificationTicketService:
    """票据绑定权威会话、用户以及认证失效代次。"""

    PREFIX = "forum:notification:ticket:v1:"
    TTL_SECONDS = 30

    def __init__(self, auth):
        self.auth = auth

    async def issue(self, token: str, expected_user_id: str) -> str:
        """先记录事件屏障，再权威校验；在途撤销令本次签发失败。"""
        if not isinstance(expected_user_id, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", expected_user_id):
            raise HTTPException(401, "会话无效")
        await self.auth.start()
        if not self.auth.ready:
            raise HTTPException(503, "认证服务暂不可用")
        # 多设备正常重连允许突发；同一账户的无界票据请求在 Redis 中限速。
        try:
            rate = await self.auth.redis.eval(
                """local count=redis.call('INCR', KEYS[1]);
                if count == 1 then redis.call('EXPIRE', KEYS[1], 10) end; return count""",
                1, self.PREFIX + "rate:" + expected_user_id,
            )
        except Exception as exc:
            raise HTTPException(503, "票据服务暂不可用") from exc
        if int(rate) > 8:
            raise HTTPException(429, "连接请求过于频繁，请稍后重试")
        prefix = self.auth.PREFIX
        generation_key = prefix + ":generation"
        sequence_key = prefix + ":event_sequence"
        try:
            generation, sequence = await self.auth.redis.mget(generation_key, sequence_key)
            generation, sequence = str(generation or "0"), int(sequence or 0)
        except Exception as exc:
            raise HTTPException(503, "票据服务暂不可用") from exc
        # 调用路由已由 current_member 执行强制权威认证；此处复用受 epoch
        # 保护的短期快照，避免每次重连重复一次权威 RPC。
        identity = await self.auth.resolve(token)
        if not identity or identity.get("user_id") != expected_user_id:
            raise HTTPException(401, "会话无效")
        session_hash = identity.get("session_id_hash")
        if not isinstance(session_hash, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", session_hash):
            raise HTTPException(503, "会话状态无效")
        ticket = secrets.token_urlsafe(32)
        key = self.PREFIX + hashlib.sha256(ticket.encode()).hexdigest()
        user_key = prefix + ":user_epoch:" + expected_user_id
        session_key = prefix + ":session_epoch:" + session_hash
        payload = json.dumps({"user_id": expected_user_id, "session_id_hash": session_hash})
        # Lua 将屏障校验、epoch 快照与写入视作一个不可分割的操作。
        try:
            accepted = await self.auth.redis.eval(
                """if (redis.call('GET', KEYS[1]) or '0') ~= ARGV[1] then return 0 end
                local u = tonumber(redis.call('GET', KEYS[2]) or '0')
                local s = tonumber(redis.call('GET', KEYS[3]) or '0')
                if u > tonumber(ARGV[2]) or s > tonumber(ARGV[2]) then return 0 end
                local v = cjson.decode(ARGV[3]); v.generation = ARGV[1]
                v.user_epoch = tostring(u); v.session_epoch = tostring(s)
                if not redis.call('SET', KEYS[4], cjson.encode(v), 'EX', ARGV[4], 'NX') then return 0 end
                return 1""",
                4, generation_key, user_key, session_key, key,
                generation, sequence, payload, self.TTL_SECONDS,
            )
        except Exception as exc:
            raise HTTPException(503, "票据服务暂不可用") from exc
        if not accepted or not self.auth.ready:
            try:
                await self.auth.redis.delete(key)
            except Exception:
                pass  # 未返回给客户端的票据将按短 TTL 失效。
            raise HTTPException(503, "认证状态已变化，请重试")
        return ticket
