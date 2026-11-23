"""校验网关签发的短期平台主体。"""

import base64
import binascii
import hashlib
import hmac
import os
import re
import time
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal


class AdminCeiling(BaseModel):
    """网关根据权限组排序计算的处置范围。"""

    model_config = ConfigDict(strict=True)
    untouchable: list[str] | Literal["all"]
    higher: list[str] | None = None


class Principal(BaseModel):
    """平台模块共享的身份头字段。"""

    model_config = ConfigDict(strict=True)
    userId: str | None = None
    mode: Literal["general", "r18"] = "general"
    showR18G: bool = False
    permissions: list[str] = Field(default_factory=list)
    newcomer: bool = False
    mutedUntil: datetime | None = None
    publishSuspendedUntil: datetime | None = None
    blockedUserIds: list[str] = Field(default_factory=list)
    blockedTagIds: list[str] = Field(default_factory=list)
    adminCeiling: AdminCeiling | None = None
    superadmin: bool = False
    iat: int = 0

    def require_user(self):
        """写操作需有效的网关账号。"""
        if self.userId is None:
            raise HTTPException(401, "需要登录")
        return self.userId

    def has_any(self, *permissions):
        """判断网关授予的站务或指定业务权限。"""
        return self.superadmin or bool(
            {"*", *permissions}.intersection(self.permissions)
        )

    def may_moderate(self, target, permissions):
        """跨账户处置需有业务权限且目标严格低于操作者。"""
        return bool(
            target
            and target != self.userId
            and self.has_any(*permissions)
            and self.adminCeiling
            and self.adminCeiling.untouchable != "all"
            and target not in self.adminCeiling.untouchable
        )

    def require_publication(self):
        """发布状态采用网关签发的权限与时限。"""
        self.require_user()
        now = datetime.now(UTC)
        if self.mutedUntil and self.mutedUntil > now:
            raise HTTPException(403, {"code": "muted", "message": "当前禁止发言"})
        if self.publishSuspendedUntil and self.publishSuspendedUntil > now:
            raise HTTPException(403, "当前暂停发布")


def verify_principal(header, secret, *, now_ms=None):
    """按固定签名格式验证主体；无效主体按全年龄匿名处理。"""
    if not secret or not header or len(header) > 65536:
        return Principal()
    try:
        payload, mac = header.split(".")
        if not re.fullmatch(r"[A-Za-z0-9_-]+", payload):
            return Principal()
        expected_mac = base64.urlsafe_b64encode(
            hmac.digest(secret.encode(), payload.encode("ascii"), hashlib.sha256)
        ).rstrip(b"=")
        if not hmac.compare_digest(mac.encode("ascii"), expected_mac):
            return Principal()
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
        principal = Principal.model_validate_json(decoded)
        current = time.time_ns() // 1_000_000 if now_ms is None else now_ms
        if abs(current - principal.iat) > 60000 or not principal.userId:
            return Principal()
        # 带时间的治理状态必须具备时区，避免比较失败或解释歧义。
        if any(
            value is not None and value.utcoffset() is None
            for value in (principal.mutedUntil, principal.publishSuspendedUntil)
        ):
            return Principal()
        return principal
    except (ValueError, UnicodeError, binascii.Error):
        return Principal()


def gateway_principal(request: Request):
    """读取通过网关身份签名解析的请求主体。"""
    return request.state.platform_principal


def verify_internal_token(request: Request):
    """校验服务调用 Forum 内部接口时携带的入站令牌。"""
    token = os.getenv("FORUM_INBOUND_INTERNAL_TOKEN", "")
    headers = request.headers.getlist("x-ody-internal-token")
    if (
        not token
        or len(headers) != 1
        or not hmac.compare_digest(headers[0].encode(), token.encode())
    ):
        raise HTTPException(401, "内部令牌无效")
