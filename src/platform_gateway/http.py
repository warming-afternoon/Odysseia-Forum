"""平台路径共同的错误外形、内容模式和分页。"""

import base64
import binascii
import os
import logging
from datetime import UTC

from fastapi import HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout

from platform_gateway.principal import verify_principal


log = logging.getLogger(__name__)

ERROR_CODES = {
    401: "unauthenticated",
    403: "forbidden",
    404: "not_found",
    409: "conflict",
    422: "validation_failed",
    501: "not_implemented",
    503: "temporarily_unavailable",
}

SORTS = {
    "new",
    "latest",
    "relevance",
    "favorites",
    "dc_reactions",
    "hot_day",
    "hot_week",
    "hot_month",
    "rising",
    "hall",
    "rookie",
}


class PlatformRoute(APIRoute):
    """验证网关签发的身份，并包装错误和模式头。"""

    def get_route_handler(self):
        """在验证请求体之前确定经签名的内容模式。"""
        handler = super().get_route_handler()
        path = self.path

        async def handle(request: Request):
            """为正常响应与校验失败回送同样的模式头。"""
            heads = request.headers.getlist("x-ody-principal")
            principal = verify_principal(
                heads[0] if len(heads) == 1 else "",
                os.getenv("FORUM_PRINCIPAL_SECRET", ""),
            )
            request.state.platform_principal = principal
            try:
                if os.getenv("FORUM_PLATFORM_ENABLED", "").lower() != "true":
                    raise HTTPException(404, "接口未启用")
                if path.startswith("/api/v1/") and (
                    request.method not in {"GET", "HEAD"}
                    or path.startswith(("/api/v1/me/", "/api/v1/admin/"))
                ):
                    principal.require_user()
                response = await handler(request)
            except RequestValidationError as exc:
                fields = {
                    ".".join(map(str, entry["loc"][1:])): entry["msg"]
                    for entry in exc.errors()
                }
                response = error_response(
                    422, {"message": "请求参数无效", "details": {"fields": fields}}
                )
            except HTTPException as exc:
                response = error_response(exc.status_code, exc.detail, exc.headers)
            except (DBAPIError, PoolTimeout) as exc:
                log.warning("平台 API 数据库暂不可用，例外类型 %s", type(exc).__name__)
                response = error_response(
                    503, "服务暂不可用，请稍后重试", {"Retry-After": "2"}
                )
            response.headers["X-Content-Mode"] = principal.mode
            response.headers["Cache-Control"] = "private, no-store"
            return response

        return handle


def error_response(status, detail, headers=None):
    """错误结构遵循平台协议，隐藏内容共用同一个 404。"""
    obj = dict(detail) if isinstance(detail, dict) else {"message": detail}
    obj.setdefault("code", ERROR_CODES.get(status, "internal_error"))
    if status == 404:
        obj["message"] = "资源不存在"
    return JSONResponse({"error": obj}, status_code=status, headers=headers)


def invalid(field, reason="字段格式无效"):
    """在协议字段位置提供校验原因。"""
    return HTTPException(
        422, {"message": reason, "details": {"fields": {field: reason}}}
    )


def timestamp(value):
    """将 Forum 储存的 UTC 时间转换为协议 ISO 时间。"""
    if value is None:
        return None
    return value.replace(tzinfo=UTC).isoformat().replace("+00:00", "Z")


def cursor_offset(cursor):
    """验证不透明分页位置。"""
    if cursor is None:
        return 0
    try:
        decoded = base64.b64decode(cursor, altchars=b"-_", validate=True).decode(
            "ascii"
        )
        if not decoded.startswith("v1:") or not decoded[3:].isdigit():
            raise ValueError
        offset = int(decoded[3:])
        if offset > 2**31 - 101:
            raise ValueError
        return offset
    except (UnicodeError, ValueError, binascii.Error) as exc:
        raise invalid("cursor") from exc


def page_result(items, offset, limit):
    """按多取一条的结果生成下一页位置。"""
    next_cursor = None
    if len(items) > limit:
        next_cursor = base64.urlsafe_b64encode(f"v1:{offset + limit}".encode()).decode()
    return {"items": items[:limit], "nextCursor": next_cursor}
