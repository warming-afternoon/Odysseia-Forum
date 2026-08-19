"""请求级 Watch 轨迹状态工具。"""

from typing import Any, MutableMapping

WATCH_REASONS_STATE_KEY = "rate_limit_watch_reasons"
WATCH_BODY_STATE_KEY = "rate_limit_watch_body"


def add_watch_reason(
    scope: MutableMapping[str, Any], reason: str, body: str | None = None
) -> None:
    """为当前请求登记 Watch 原因和可选请求体。"""
    state = scope.setdefault("state", {})
    reasons = state.setdefault(WATCH_REASONS_STATE_KEY, [])
    if reason not in reasons:
        reasons.append(reason)
    if body is not None and WATCH_BODY_STATE_KEY not in state:
        state[WATCH_BODY_STATE_KEY] = body


def get_watch_reasons(scope: MutableMapping[str, Any]) -> list[str]:
    """读取当前请求登记的 Watch 原因。"""
    state = scope.get("state", {})
    return list(state.get(WATCH_REASONS_STATE_KEY, []))


def get_watch_body(scope: MutableMapping[str, Any]) -> str | None:
    """读取当前请求登记的请求体。"""
    state = scope.get("state", {})
    body = state.get(WATCH_BODY_STATE_KEY)
    return body if isinstance(body, str) else None
