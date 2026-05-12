"""ASGI tests for GET /api/v1/valuation/{symbol}."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.api.v1.endpoints.valuation import _db_session
from app.core.config import settings
from app.integration.sec_edgar_client import get_sec_edgar_client
from app.integration.yfinance_client import get_yfinance_client
from app.main import app
from app.services import valuation_service as vs
from tests._score_helpers import make_facts


TEST_KEY = "test-uuid-key-0000"


class _NoLiveYF:
    """Stub YFinance client returning None — forces M1+M4 unavailable."""

    async def get_info(self, _symbol):
        return None

    async def get_history(self, _symbol, **_kwargs):
        return None


@pytest_asyncio.fixture()
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "FINTERMINAL_API_KEY", TEST_KEY)

    async def _override_db():
        yield db

    sec_fake = AsyncMock()
    app.dependency_overrides[_db_session] = _override_db
    app.dependency_overrides[get_sec_edgar_client] = lambda: sec_fake
    app.dependency_overrides[get_yfinance_client] = lambda: _NoLiveYF()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.fake_sec = sec_fake  # type: ignore[attr-defined]
        yield c

    app.dependency_overrides.clear()


@pytest.mark.integration
async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/valuation/AAPL")
    assert resp.status_code == 401


@pytest.mark.integration
async def test_endpoint_returns_indetermine_with_graham_value(
    client: httpx.AsyncClient, monkeypatch,
) -> None:
    """V1 happy path: Graham works, verdict INDÉTERMINÉ (single method)."""
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", 1)],
        "StockholdersEquity":           [("2024-12-31", 10)],
        "CommonStockSharesOutstanding": [("2024-12-31", 1)],
    })
    monkeypatch.setattr(
        vs.financials_service,
        "get_facts_payload",
        AsyncMock(return_value=(320193, "Apple Inc.", facts)),
    )
    resp = await client.get(
        "/api/v1/valuation/AAPL",
        headers={"X-Auth-Token": TEST_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["verdict"] == "INDÉTERMINÉ"
    assert body["confidence"] == "N/A"
    assert body["n_methods"] == 1
    # Graham exposed in methods
    assert body["methods"]["graham_number"]["available"] is True
    assert Decimal(body["methods"]["graham_number"]["fair_value"]) == Decimal("15")
    # The 3 unavailable methods carry an explanatory reason.
    # (Step 5: with the no-live YFinance stub, M1+M4 fall back to
    # available=False with reasons mentioning YFinance/SEC inputs;
    # M2 still carries the "V1 — FMP not integrated" stub reason.)
    for name in ("vs_historical_5y", "vs_sector", "analyst_target"):
        assert body["methods"][name]["available"] is False
        assert body["methods"][name]["reason"]


@pytest.mark.integration
async def test_endpoint_non_us_ticker_returns_indetermine(
    client: httpx.AsyncClient, monkeypatch,
) -> None:
    monkeypatch.setattr(
        vs.financials_service,
        "get_facts_payload",
        AsyncMock(return_value=None),
    )
    resp = await client.get(
        "/api/v1/valuation/AIXA.DE",
        headers={"X-Auth-Token": TEST_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "INDÉTERMINÉ"
    assert body["n_methods"] == 0
    assert any("non-US" in w or "INVESTISSABLE_NON_US_LIMITATION" in w
               for w in body["warnings"])


@pytest.mark.integration
async def test_endpoint_normalises_symbol_to_uppercase(
    client: httpx.AsyncClient, monkeypatch,
) -> None:
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", 1)],
        "StockholdersEquity":           [("2024-12-31", 10)],
        "CommonStockSharesOutstanding": [("2024-12-31", 1)],
    })
    monkeypatch.setattr(
        vs.financials_service,
        "get_facts_payload",
        AsyncMock(return_value=(320193, "Apple Inc.", facts)),
    )
    resp = await client.get(
        "/api/v1/valuation/aapl",
        headers={"X-Auth-Token": TEST_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"
