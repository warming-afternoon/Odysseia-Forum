"""验证跨语言签名、共享身份签名和平台错误协议。"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from platform_gateway.principal import AdminCeiling, Principal, verify_principal
from platform_gateway.routes import router


VECTOR = (
    "eyJ1c2VySWQiOiJ1X3Rlc3QiLCJtb2RlIjoicjE4Iiwic2hvd1IxOEciOmZhbHNlLCJwZXJtaXNzaW9ucyI6WyJjaGF0LnNlbmQiXSwibmV3Y29tZXIiOmZhbHNlLCJtdXRlZFVudGlsIjpudWxsLCJibG9ja2VkVXNlcklkcyI6W10sImJsb2NrZWRUYWdJZHMiOltdLCJpYXQiOjE3NjMwOTI4MDAwMDB9"
    ".lOEwndIbLOtLLrItLJDZI04ByMsm_X3vPPTcSDUTHFA"
)
ISSUED_AT = 1763092800000


def test_cross_language_principal_vector_and_window():
    """协议向量和时间边界应由不同语言实现产生相同结果。"""
    principal = verify_principal(VECTOR, "test-secret", now_ms=ISSUED_AT + 60000)
    assert principal.userId == "u_test"
    assert principal.mode == "r18"
    assert principal.permissions == ["chat.send"]
    assert (
        verify_principal(VECTOR, "test-secret", now_ms=ISSUED_AT + 60001) == Principal()
    )
    assert (
        verify_principal(VECTOR, "test-secret", now_ms=ISSUED_AT - 60001) == Principal()
    )
    assert verify_principal(VECTOR, "other-secret", now_ms=ISSUED_AT) == Principal()


def test_cross_account_moderation_is_fail_closed():
    """有效管理权限仍须有排序上限且目标不在保护名单内。"""
    principal = Principal(userId="u_staff", permissions=["works.manage_any"])
    permission = {"works.manage_any"}
    assert not principal.may_moderate("u_author", permission)
    principal.adminCeiling = AdminCeiling(untouchable=["u_peer"])
    assert principal.may_moderate("u_author", permission)
    assert not principal.may_moderate("u_peer", permission)
    assert not Principal(userId="u_staff", permissions=["site.admin"]).has_any(
        "trash.manage"
    )
    assert not principal.may_moderate("u_staff", permission)
    principal.adminCeiling = AdminCeiling(untouchable="all")
    assert not principal.may_moderate("u_author", permission)


def test_platform_routes_share_forum_signing_key(monkeypatch):
    """作品和书单路由均接受相同的网关身份签名。"""
    monkeypatch.setenv("FORUM_PLATFORM_ENABLED", "true")
    monkeypatch.setenv("FORUM_PRINCIPAL_SECRET", "test-secret")
    monkeypatch.setattr(
        "platform_gateway.principal.time.time_ns", lambda: ISSUED_AT * 1_000_000
    )
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    headers = {"X-Ody-Principal": VECTOR}
    invalid_work = client.post("/api/v1/works", headers=headers, json={"invalid": 1})
    invalid_collection = client.post(
        "/api/v1/collections", headers=headers, json={"invalid": 1}
    )
    assert invalid_work.status_code == 422
    assert invalid_collection.status_code == 422
    assert invalid_work.headers["x-content-mode"] == "r18"
    assert invalid_collection.headers["x-content-mode"] == "r18"
    assert invalid_work.json()["error"]["code"] == "validation_failed"
