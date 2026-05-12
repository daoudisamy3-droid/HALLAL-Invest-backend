"""ASGI tests for GET /api/v1/investissable/{symbol}."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.api.v1.endpoints.investissable import _db_session
from app.core.config import settings
from app.integration.halal_terminal_client import get_halal_terminal_client
from app.integration.sec_edgar_client import get_sec_edgar_client
from app.main import app
from app.schemas.shariah import ShariahReport
from app.services import investissable_service


TEST_KEY = "test-uuid-key-0000"


@pytest_asyncio.fixture()
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "FINTERMINAL_API_KEY", TEST_KEY)

    async def _override_db():
        yield db

    halal_fake = AsyncMock()
    sec_fake = AsyncMock()

    app.dependency_overrides[_db_session] = _override_db
    app.dependency_overrides[get_halal_terminal_client] = lambda: halal_fake
    app.dependency_overrides[get_sec_edgar_client] = lambda: sec_fake

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.fake_sec = sec_fake  # type: ignore[attr-defined]
        c.fake_halal = halal_fake  # type: ignore[attr-defined]
        yield c

    app.dependency_overrides.clear()


@pytest.mark.integration
async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/investissable/AAPL")
    assert resp.status_code == 401


@pytest.mark.integration
async def test_aaoifi_fail_returns_non_verdict_in_body(
    client: httpx.AsyncClient, monkeypatch,
) -> None:
    monkeypatch.setattr(
        investissable_service.shariah_service,
        "screen_with_personal_thresholds",
        AsyncMock(return_value=ShariahReport(
            symbol="AAPL",
            verdict="FAIL",
            source="Halal Terminal API (aggregate verdict)",
            reason="business screen failed",
        )),
    )
    resp = await client.get(
        "/api/v1/investissable/AAPL",
        headers={"X-Auth-Token": TEST_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "NON"
    assert body["blocked_at"] == "aaoifi"


@pytest.mark.integration
async def test_symbol_normalised_to_uppercase(
    client: httpx.AsyncClient, monkeypatch,
) -> None:
    monkeypatch.setattr(
        investissable_service.shariah_service,
        "screen_with_personal_thresholds",
        AsyncMock(return_value=ShariahReport(
            symbol="AAPL",
            verdict="NOT_COVERED",
            source="Halal Terminal API (aggregate verdict)",
            reason="not covered",
        )),
    )
    resp = await client.get(
        "/api/v1/investissable/aapl",
        headers={"X-Auth-Token": TEST_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"


@pytest.mark.integration
async def test_non_us_ticker_returns_incertain_verdict(
    client: httpx.AsyncClient, monkeypatch,
) -> None:
    """Non-US ticker → INCERTAIN (V1 SEC EDGAR limitation)."""
    monkeypatch.setattr(
        investissable_service.shariah_service,
        "screen_with_personal_thresholds",
        AsyncMock(return_value=ShariahReport(
            symbol="AIXA.DE",
            verdict="PASS",
            source="Halal Terminal API (aggregate verdict)",
        )),
    )
    client.fake_sec.lookup_cik = AsyncMock(return_value=None)  # type: ignore[attr-defined]

    resp = await client.get(
        "/api/v1/investissable/AIXA.DE",
        headers={"X-Auth-Token": TEST_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "INCERTAIN"
    assert body["data_completeness"] == "INSUFFICIENT"
    # Warning should mention the V1 limitation.
    assert any("non-US" in w or "SEC EDGAR" in w for w in body["warnings"])
