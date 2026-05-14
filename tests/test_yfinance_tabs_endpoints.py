"""HTTP tests for the Step 8 Phase C tabs (/calendar /management /holders).

YFinance is mocked at the service layer. Each endpoint must always
return 200 — never propagate Yahoo flakiness as 5xx. ``available=false``
is the documented signal for "data missing".
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.core.config import settings
from app.integration.yfinance_client import get_yfinance_client
from app.main import app
from app.services import yfinance_service


TEST_KEY = "test-uuid-key-0000"


@pytest_asyncio.fixture()
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "FINTERMINAL_API_KEY", TEST_KEY)
    app.dependency_overrides[get_yfinance_client] = lambda: AsyncMock()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()


def _auth():
    return {"X-Auth-Token": TEST_KEY}


# ─── /calendar ──────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_calendar_returns_200_with_available_true(client, monkeypatch):
    fake_payload = {
        "Earnings Date": "2026-08-01",
        "Ex-Dividend Date": "2026-08-15",
        "earnings_history": [
            {"quarter": "2024Q4", "eps_estimate": 1.5, "eps_actual": 1.6},
            {"quarter": "2024Q3", "eps_estimate": 1.2, "eps_actual": 1.3},
        ],
        "dividends": [
            {"date": "2024-11-15", "amount": 0.25},
            {"date": "2024-08-15", "amount": 0.24},
        ],
    }
    async def fake_get_calendar(*_a, **_kw):
        return fake_payload
    monkeypatch.setattr(yfinance_service, "get_calendar", fake_get_calendar)

    resp = await client.get("/api/v1/calendar/AAPL", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["available"] is True
    assert body["next_earnings_date"] == "2026-08-01"
    assert body["ex_dividend_date"] == "2026-08-15"
    assert len(body["earnings_history"]) == 2
    assert len(body["dividends"]) == 2


@pytest.mark.integration
async def test_calendar_returns_200_with_available_false_when_yfinance_fails(client, monkeypatch):
    async def fake_get_calendar(*_a, **_kw):
        return None
    monkeypatch.setattr(yfinance_service, "get_calendar", fake_get_calendar)
    resp = await client.get("/api/v1/calendar/ZZZZ", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["reason"]
    assert body["earnings_history"] == []


@pytest.mark.integration
async def test_calendar_unauthenticated_401(client):
    resp = await client.get("/api/v1/calendar/AAPL")
    assert resp.status_code == 401


# ─── /management ────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_management_returns_officers_list(client, monkeypatch):
    fake_officers = [
        {"name": "Tim Cook", "title": "CEO", "age": 63, "year_born": 1960,
         "total_pay": 16000000, "exercised_value": 0, "unexercised_value": 0,
         "fiscal_year": 2024},
        {"name": "Luca Maestri", "title": "CFO", "age": 60, "year_born": 1963,
         "total_pay": 5000000, "exercised_value": 0, "unexercised_value": 0,
         "fiscal_year": 2024},
    ]
    async def fake_get_management(*_a, **_kw):
        return fake_officers
    monkeypatch.setattr(yfinance_service, "get_management", fake_get_management)

    resp = await client.get("/api/v1/management/AAPL", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is True
    assert len(body["officers"]) == 2
    assert body["officers"][0]["name"] == "Tim Cook"
    assert body["officers"][0]["title"] == "CEO"


@pytest.mark.integration
async def test_management_returns_unavailable_on_empty(client, monkeypatch):
    async def fake_get_management(*_a, **_kw):
        return None
    monkeypatch.setattr(yfinance_service, "get_management", fake_get_management)
    resp = await client.get("/api/v1/management/ZZZZ", headers=_auth())
    body = resp.json()
    assert resp.status_code == 200
    assert body["available"] is False
    assert body["officers"] == []


# ─── /holders ───────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_holders_returns_composite(client, monkeypatch):
    fake_payload = {
        "major_holders": [
            {"label": "% of Shares Held by Insiders", "value": "0.07%"},
            {"label": "% of Shares Held by Institutions", "value": "61.5%"},
        ],
        "institutional_holders": [
            {"Holder": "Vanguard Group Inc.", "Shares": 1300000000, "% Out": "0.087"},
            {"Holder": "BlackRock Inc.", "Shares": 1050000000, "% Out": "0.069"},
        ],
        "insider_transactions": [
            {"Insider": "COOK TIMOTHY D", "Transaction": "Sale", "Shares": 200000,
             "Value": 50000000, "Date": "2024-10-15"},
        ],
    }
    async def fake_get_holders(*_a, **_kw):
        return fake_payload
    monkeypatch.setattr(yfinance_service, "get_holders", fake_get_holders)

    resp = await client.get("/api/v1/holders/AAPL", headers=_auth())
    body = resp.json()
    assert resp.status_code == 200
    assert body["available"] is True
    assert len(body["major_holders"]) == 2
    assert len(body["institutional_holders"]) == 2
    assert len(body["insider_transactions"]) == 1


@pytest.mark.integration
async def test_holders_normalises_symbol_to_uppercase(client, monkeypatch):
    captured: dict[str, str] = {}
    async def fake_get_holders(ticker, *_a, **_kw):
        captured["ticker"] = ticker
        return {"major_holders": [{"x": 1}], "institutional_holders": [], "insider_transactions": []}
    monkeypatch.setattr(yfinance_service, "get_holders", fake_get_holders)

    resp = await client.get("/api/v1/holders/aapl", headers=_auth())
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"
    assert captured["ticker"] == "AAPL"
