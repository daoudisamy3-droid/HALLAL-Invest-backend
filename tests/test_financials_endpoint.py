"""Integration tests for GET /api/v1/financials/{ticker}.

Wires the real FastAPI app via ASGITransport, overrides:
  - `_db_session` (so the endpoint uses the conftest test session)
  - `get_sec_edgar_client` (so we don't hit the network)
  - `settings.FINTERMINAL_API_KEY` (so auth works with a known token)
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.api.v1.endpoints.financials import _db_session
from app.core.config import settings
from app.integration.sec_edgar_client import get_sec_edgar_client
from app.main import app


TEST_KEY = "test-uuid-key-0000"


def _pass_facts() -> dict:
    return {
        "cik": 320193,
        "entityName": "Apple Inc.",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [{
                            "start": "2023-09-25", "end": "2024-09-28",
                            "val": 391035000000, "fy": 2024, "fp": "FY",
                            "form": "10-K", "filed": "2024-11-01",
                            "accn": "0000320193-24-000123",
                        }]
                    }
                }
            }
        },
    }


@pytest_asyncio.fixture()
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "FINTERMINAL_API_KEY", TEST_KEY)

    async def _override_db():
        yield db

    fake_client = AsyncMock()
    app.dependency_overrides[_db_session] = _override_db
    app.dependency_overrides[get_sec_edgar_client] = lambda: fake_client

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        c.fake_sec_client = fake_client  # type: ignore[attr-defined]
        yield c

    app.dependency_overrides.clear()


@pytest.mark.integration
async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/financials/AAPL")
    assert resp.status_code == 401


@pytest.mark.integration
async def test_authenticated_available_verdict(client: httpx.AsyncClient) -> None:
    client.fake_sec_client.lookup_cik.return_value = (320193, "Apple Inc.")  # type: ignore[attr-defined]
    client.fake_sec_client.get_company_facts.return_value = _pass_facts()  # type: ignore[attr-defined]

    resp = await client.get(
        "/api/v1/financials/AAPL",
        headers={"X-Auth-Token": TEST_KEY},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert body["verdict"] == "AVAILABLE"
    assert body["cik"] == 320193
    assert body["entity_name"] == "Apple Inc."
    assert body["fiscal_year"] == 2024
    assert body["revenues"] == "391035000000"  # Decimal → string
    assert body["source"] == "SEC EDGAR API"
    assert body["cached"] is False


@pytest.mark.integration
async def test_endpoint_returns_not_covered_for_non_us_ticker(
    client: httpx.AsyncClient,
) -> None:
    client.fake_sec_client.lookup_cik.return_value = None  # type: ignore[attr-defined]

    resp = await client.get(
        "/api/v1/financials/AIXA.DE",
        headers={"X-Auth-Token": TEST_KEY},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["verdict"] == "NOT_COVERED"
    assert body["cik"] is None
    assert "non-US" in body["reason"] or "univers SEC" in body["reason"]


@pytest.mark.integration
async def test_endpoint_normalises_ticker_to_uppercase(
    client: httpx.AsyncClient,
) -> None:
    client.fake_sec_client.lookup_cik.return_value = (320193, "Apple Inc.")  # type: ignore[attr-defined]
    client.fake_sec_client.get_company_facts.return_value = _pass_facts()  # type: ignore[attr-defined]

    resp = await client.get(
        "/api/v1/financials/aapl",
        headers={"X-Auth-Token": TEST_KEY},
    )

    assert resp.status_code == 200
    assert resp.json()["ticker"] == "AAPL"
