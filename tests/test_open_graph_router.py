from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from api.v1.routers import open_graph


@pytest.mark.asyncio
async def test_missing_service_token_configuration_returns_503(monkeypatch):
    """服务端未配置令牌时接口不可用。"""
    monkeypatch.setattr(open_graph, "_service_token", None)

    with pytest.raises(HTTPException) as exc_info:
        await open_graph.require_open_graph_service_token("Bearer anything")

    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_missing_or_wrong_service_token_returns_401(monkeypatch):
    """请求缺失或携带错误令牌时统一拒绝。"""
    monkeypatch.setattr(open_graph, "_service_token", "correct-secret")

    for authorization in (None, "Bearer wrong-secret", "Basic correct-secret"):
        with pytest.raises(HTTPException) as exc_info:
            await open_graph.require_open_graph_service_token(authorization)
        assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_private_and_missing_booklists_share_same_404(monkeypatch):
    """服务层隐藏的私有或不存在书单都映射为同一 404。"""
    service = MagicMock()
    service.get_share_metadata = AsyncMock(return_value=None)
    monkeypatch.setattr(open_graph, "_open_graph_service", service)

    with pytest.raises(HTTPException) as exc_info:
        await open_graph.get_booklist_share_metadata(99)

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "书单不存在"
