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


def _compliant_payload(symbol: str) -> dict:
    return {
        "symbol": symbol,
        "overall_status": "compliant",
        "ratios": {
            "debt_to_marketcap": 0.020,
            "debt_to_assets": 0.085,
            "cash_to_marketcap": 0.0164,
            "impure_revenue_ratio": 0.0023,
            "interest_income_ratio": 0.0018,
            "receivables_to_assets": 0.082,
            "non_compliant_assets_ratio": 0.046,
        },
        "methodologies": {
            m: {"status": "compliant", "failed_ratios": []}
            for m in ("AAOIFI", "DJIM", "FTSE", "MSCI", "S&P")
        },
        "as_of_date": "2026-04-30",
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
    client.fake_halal_client.screen.return_value = _compliant_payload("AAPL")  # type: ignore[attr-defined]

    resp = await client.get(
        "/api/v1/shariah/AAPL",
        headers={"X-Auth-Token": TEST_KEY},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["verdict"] == "PASS"
    assert body["failed_checks"] == []
    assert body["source"] == "Halal Terminal API + custom thresholds"
    assert body["cached"] is False
    assert "checks" in body and "debt_to_marketcap" in body["checks"]
    assert body["checks"]["debt_to_marketcap"]["threshold"] == 0.30


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
    assert "indisponible" in body["reason"].lower()


@pytest.mark.integration
async def test_endpoint_symbol_is_normalised_to_uppercase(
    client: httpx.AsyncClient,
) -> None:
    client.fake_halal_client.screen.return_value = _compliant_payload("AAPL")  # type: ignore[attr-defined]

    resp = await client.get(
        "/api/v1/shariah/aapl",
        headers={"X-Auth-Token": TEST_KEY},
    )

    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"
