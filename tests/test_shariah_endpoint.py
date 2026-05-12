"""Integration tests for GET /api/v1/shariah/{symbol}.

Wires the real FastAPI app via ASGITransport, overrides:
  - `_db_session` (so the endpoint uses the SAVEPOINT test session)
  - `get_halal_terminal_client` (so we don't hit the network)
  - `settings.FINTERMINAL_API_KEY` (so auth works with a known token)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.api.v1.endpoints.shariah import _db_session
from app.core.config import settings
from app.core.exceptions import HalalTerminalError
from app.integration.halal_terminal_client import get_halal_terminal_client
from app.main import app


TEST_KEY = "test-uuid-key-0000"


def _aggregate_pass_payload(symbol: str) -> dict:
    """Free-tier Halal Terminal payload — aggregate verdict shape."""
    return {
        "symbol": symbol,
        "name": f"{symbol} Inc.",
        "is_compliant": True,
        "business_screen_pass": True,
        "business_screen_reason": "Business activity is compliant.",
        "financial_screen_pass": True,
        "shariah_compliance_status": None,
    }


@pytest_asyncio.fixture()
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "FINTERMINAL_API_KEY", TEST_KEY)

    async def _override_db():
        yield db

    fake_client = AsyncMock()
    app.dependency_overrides[_db_session] = _override_db
    app.dependency_overrides[get_halal_terminal_client] = lambda: fake_client

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.fake_halal_client = fake_client  # type: ignore[attr-defined]
        yield c

    app.dependency_overrides.clear()


@pytest.mark.integration
async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/shariah/AAPL")
    assert resp.status_code == 401


@pytest.mark.integration
async def test_authenticated_pass_verdict(client: httpx.AsyncClient) -> None:
    client.fake_halal_client.screen.return_value = _aggregate_pass_payload("AAPL")  # type: ignore[attr-defined]

    resp = await client.get(
        "/api/v1/shariah/AAPL",
        headers={"X-Auth-Token": TEST_KEY},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["verdict"] == "PASS"
    assert body["failed_checks"] == []
    assert body["source"] == "Halal Terminal API (aggregate verdict)"
    assert body["cached"] is False
    # Free-tier deviation: no raw ratios surfaced → checks dict is empty
    assert body["checks"] == {}


@pytest.mark.integration
async def test_endpoint_returns_error_verdict_on_upstream_failure(
    client: httpx.AsyncClient,
) -> None:
    client.fake_halal_client.screen.side_effect = HalalTerminalError("upstream 500")  # type: ignore[attr-defined]

    resp = await client.get(
        "/api/v1/shariah/AAPL",
        headers={"X-Auth-Token": TEST_KEY},
    )

    # Endpoint still returns 200 — the verdict carries the failure
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "ERROR"
    assert body["source"] == "Halal Terminal API (error)"
    assert "indisponible" in body["reason"].lower()


@pytest.mark.integration
async def test_endpoint_symbol_is_normalised_to_uppercase(
    client: httpx.AsyncClient,
) -> None:
    client.fake_halal_client.screen.return_value = _aggregate_pass_payload("AAPL")  # type: ignore[attr-defined]

    resp = await client.get(
        "/api/v1/shariah/aapl",
        headers={"X-Auth-Token": TEST_KEY},
    )

    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"


@pytest.mark.integration
async def test_endpoint_returns_not_covered_for_ticker_unknown(
    client: httpx.AsyncClient,
) -> None:
    """Free-tier ticker_unknown response surfaces as NOT_COVERED in the body."""
    client.fake_halal_client.screen.return_value = {  # type: ignore[attr-defined]
        "symbol": "ZZZBIDON",
        "is_compliant": None,
        "error": "ticker_unknown",
        "error_message": "Symbol 'ZZZBIDON' is not in our universe; no Shariah verdict available.",
    }

    resp = await client.get(
        "/api/v1/shariah/ZZZBIDON",
        headers={"X-Auth-Token": TEST_KEY},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "NOT_COVERED"
    assert "ZZZBIDON" in body["reason"]
    assert body["source"] == "Halal Terminal API (aggregate verdict)"
