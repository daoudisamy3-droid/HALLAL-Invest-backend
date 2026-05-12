"""HTTP/ASGI tests for /api/v1/portfolio/* endpoints.

Note on Decimal comparison: Postgres ``NUMERIC(18,8)`` round-trips through
the DB and serialises in JSON with the full precision (e.g. ``"10.00000000"``).
We compare via :class:`decimal.Decimal` so trailing-zero artefacts don't
fail the assertions while keeping exact arithmetic.
"""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.api.v1.endpoints.portfolio import _db_session
from app.core.config import settings
from app.main import app


TEST_KEY = "test-uuid-key-0000"


@pytest_asyncio.fixture()
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "FINTERMINAL_API_KEY", TEST_KEY)

    async def _override_db():
        yield db

    app.dependency_overrides[_db_session] = _override_db

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


def _auth():
    return {"X-Auth-Token": TEST_KEY}


def _tx_body(
    qty: str | int,
    price: str | int,
    fee: str | int = 0,
    *,
    symbol: str = "AAPL",
    currency: str = "USD",
    date: str = "2025-01-01",
) -> dict:
    return {
        "symbol": symbol,
        "currency": currency,
        "date": date,
        "qty": str(qty),
        "price": str(price),
        "fee": str(fee),
    }


# ─── Auth ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/portfolio/positions")
    assert resp.status_code == 401


# ─── POST /transactions — happy path ────────────────────────────────────────


@pytest.mark.integration
async def test_post_transaction_creates_position_and_returns_201(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=10, price=100, fee=1),
        headers=_auth(),
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["currency"] == "USD"
    assert Decimal(body["qty"]) == Decimal("10")
    assert Decimal(body["price"]) == Decimal("100")
    assert Decimal(body["fee"]) == Decimal("1")
    # Sanity: id + position_id are UUIDs
    assert len(body["id"]) == 36
    assert len(body["position_id"]) == 36


@pytest.mark.integration
async def test_symbol_normalised_to_uppercase(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=1, price=1, symbol="aapl"),
        headers=_auth(),
    )
    assert resp.status_code == 201
    assert resp.json()["symbol"] == "AAPL"


# ─── POST /transactions — input validation ──────────────────────────────────


@pytest.mark.integration
async def test_post_qty_zero_returns_422(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=0, price=100),
        headers=_auth(),
    )
    assert resp.status_code == 422


@pytest.mark.integration
async def test_post_price_zero_returns_422(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=10, price=0),
        headers=_auth(),
    )
    assert resp.status_code == 422


@pytest.mark.integration
async def test_post_negative_fee_returns_422(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=10, price=100, fee=-1),
        headers=_auth(),
    )
    assert resp.status_code == 422


# ─── POST /transactions — service-level rejections (400) ────────────────────


@pytest.mark.integration
async def test_post_non_usd_currency_returns_400(client: httpx.AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=10, price=100, currency="EUR"),
        headers=_auth(),
    )
    assert resp.status_code == 400
    assert "Multi-currency" in resp.json()["detail"]


@pytest.mark.integration
async def test_post_oversell_returns_400(client: httpx.AsyncClient) -> None:
    # First BUY 10
    await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=10, price=100, date="2025-01-01"),
        headers=_auth(),
    )
    # SELL 15 (oversell) — should be rejected
    resp = await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=-15, price=120, date="2025-01-02"),
        headers=_auth(),
    )
    assert resp.status_code == 400
    assert "only 10" in resp.json()["detail"]


# ─── GET /positions and /transactions and /summary ──────────────────────────


@pytest.mark.integration
async def test_get_positions_empty_returns_empty_list(
    client: httpx.AsyncClient,
) -> None:
    resp = await client.get("/api/v1/portfolio/positions", headers=_auth())
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.integration
async def test_get_summary_empty(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/portfolio/summary", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert Decimal(body["total_invested"]) == Decimal("0")
    assert body["n_positions"] == 0
    assert body["n_transactions"] == 0
    assert body["pnl_pct"] is None
    assert body["currency"] == "USD"


@pytest.mark.integration
async def test_get_positions_after_transactions(client: httpx.AsyncClient) -> None:
    # BUY 10 AAPL @ 100, fee 1
    await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=10, price=100, fee=1),
        headers=_auth(),
    )

    resp = await client.get("/api/v1/portfolio/positions", headers=_auth())
    assert resp.status_code == 200
    positions = resp.json()
    assert len(positions) == 1
    p = positions[0]
    assert p["symbol"] == "AAPL"
    assert Decimal(p["quantity"]) == Decimal("10")
    assert Decimal(p["avg_cost"]) == Decimal("100.1")
    assert Decimal(p["cost_basis"]) == Decimal("1001")


@pytest.mark.integration
async def test_get_transactions_chronological(client: httpx.AsyncClient) -> None:
    await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=10, price=100, date="2025-01-02"),
        headers=_auth(),
    )
    await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=5, price=110, date="2025-01-01"),
        headers=_auth(),
    )

    resp = await client.get("/api/v1/portfolio/transactions", headers=_auth())
    assert resp.status_code == 200
    txs = resp.json()
    assert len(txs) == 2
    assert txs[0]["date"] == "2025-01-01"
    assert txs[1]["date"] == "2025-01-02"


# ─── DELETE /transactions/{id} ──────────────────────────────────────────────


@pytest.mark.integration
async def test_delete_transaction_204(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/v1/portfolio/transactions",
        json=_tx_body(qty=10, price=100),
        headers=_auth(),
    )
    tx_id = created.json()["id"]

    resp = await client.delete(
        f"/api/v1/portfolio/transactions/{tx_id}",
        headers=_auth(),
    )
    assert resp.status_code == 204
    # Cascade: position also gone
    positions = await client.get("/api/v1/portfolio/positions", headers=_auth())
    assert positions.json() == []


@pytest.mark.integration
async def test_delete_unknown_transaction_404(client: httpx.AsyncClient) -> None:
    resp = await client.delete(
        "/api/v1/portfolio/transactions/00000000-0000-0000-0000-000000000000",
        headers=_auth(),
    )
    assert resp.status_code == 404
