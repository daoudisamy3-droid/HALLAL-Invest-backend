"""
Integration tests for the anti-bot API key guard  §9.6.

Three behaviours verified:
  1. GET /health → 200 with no X-Auth-Token (public endpoint)
  2. GET /api/v1/ping without X-Auth-Token → 401
  3. GET /api/v1/ping with correct X-Auth-Token → 200 (business response)
"""

import pytest
import pytest_asyncio
import httpx
from httpx import ASGITransport

from app.main import app
from app.core.config import settings

TEST_KEY = "test-uuid-key-0000"


@pytest_asyncio.fixture()
async def client(monkeypatch: pytest.MonkeyPatch):
    """ASGI test client with FINTERMINAL_API_KEY patched to a known value."""
    monkeypatch.setattr(settings, "FINTERMINAL_API_KEY", TEST_KEY)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


@pytest.mark.integration
async def test_health_is_public(client: httpx.AsyncClient) -> None:
    """GET /health returns 200 without any auth header."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.integration
async def test_api_v1_without_token_returns_401(client: httpx.AsyncClient) -> None:
    """GET /api/v1/ping without X-Auth-Token → 401."""
    resp = await client.get("/api/v1/ping")
    assert resp.status_code == 401


@pytest.mark.integration
async def test_api_v1_with_correct_token_returns_200(client: httpx.AsyncClient) -> None:
    """GET /api/v1/ping with correct X-Auth-Token → 200 (not 401)."""
    resp = await client.get("/api/v1/ping", headers={"X-Auth-Token": TEST_KEY})
    assert resp.status_code == 200
    assert resp.json() == {"pong": True}
