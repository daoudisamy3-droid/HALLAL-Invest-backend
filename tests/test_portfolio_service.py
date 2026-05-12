"""Service-layer tests for app/services/portfolio_service.py.

Five canonical P&L scenarios (A → E) are asserted with **exact Decimal
equality**. No floats, no ``pytest.approx``. If any of these fails, the
P&L algorithm is broken at the core — fix it, don't loosen the assertion.

DB fixture is the real Postgres session from conftest (function-scoped,
truncated on teardown).
"""

from __future__ import annotations

import uuid
from datetime import date as date_type
from decimal import Decimal

import pytest
from sqlalchemy import select

from app.models import Position, Transaction
from app.schemas.portfolio import TransactionCreate
from app.services import portfolio_service as svc


# ─── Helpers ────────────────────────────────────────────────────────────────


def _tx(
    qty: str | int,
    price: str | int,
    fee: str | int = 0,
    *,
    date: str = "2025-01-01",
    symbol: str = "AAPL",
    currency: str = "USD",
) -> TransactionCreate:
    """Build a TransactionCreate with Decimal-safe coercions (str→Decimal)."""
    return TransactionCreate(
        symbol=symbol,
        currency=currency,
        date=date_type.fromisoformat(date),
        qty=Decimal(str(qty)),
        price=Decimal(str(price)),
        fee=Decimal(str(fee)),
    )


async def _only_position(db) -> dict:
    """Return derived fields of the (single) position as a dict for assertions."""
    positions = await svc.list_positions(db)
    assert len(positions) == 1, f"expected exactly 1 position, got {len(positions)}"
    p = positions[0]
    return {
        "quantity": p.quantity,
        "avg_cost": p.avg_cost,
        "cost_basis": p.cost_basis,
        "last_price": p.last_price,
        "current_value": p.current_value,
        "unrealized_pnl": p.unrealized_pnl,
        "realized_pnl": p.realized_pnl,
        "transactions_count": p.transactions_count,
    }


# ─── Scenario A — simple BUY ────────────────────────────────────────────────


@pytest.mark.integration
async def test_scenario_a_simple_buy(db) -> None:
    """BUY 10 AAPL @ $100, fee $1 → qty=10, avg_cost=100.1, cost_basis=1001."""
    await svc.create_transaction(db, _tx(qty=10, price=100, fee=1))
    p = await _only_position(db)

    assert p["quantity"] == Decimal("10")
    assert p["avg_cost"] == Decimal("100.1")
    assert p["cost_basis"] == Decimal("1001")
    assert p["last_price"] == Decimal("100")
    assert p["current_value"] == Decimal("1000")        # 10 × 100
    assert p["unrealized_pnl"] == Decimal("-1")         # 1000 − 1001
    assert p["realized_pnl"] == Decimal("0")
    assert p["transactions_count"] == 1


# ─── Scenario B — weighted average over two BUYs ────────────────────────────


@pytest.mark.integration
async def test_scenario_b_weighted_avg_two_buys(db) -> None:
    """BUY 10 @ 100, then BUY 10 @ 120 → qty=20, avg_cost=110, cost_basis=2200."""
    await svc.create_transaction(db, _tx(qty=10, price=100, date="2025-01-01"))
    await svc.create_transaction(db, _tx(qty=10, price=120, date="2025-01-02"))
    p = await _only_position(db)

    assert p["quantity"] == Decimal("20")
    assert p["avg_cost"] == Decimal("110")
    assert p["cost_basis"] == Decimal("2200")
    assert p["last_price"] == Decimal("120")
    assert p["current_value"] == Decimal("2400")        # 20 × 120
    assert p["unrealized_pnl"] == Decimal("200")        # 2400 − 2200
    assert p["realized_pnl"] == Decimal("0")
    assert p["transactions_count"] == 2


# ─── Scenario C — partial sell, realized P&L (no sell fee) ──────────────────


@pytest.mark.integration
async def test_scenario_c_partial_sell_realized_pnl(db) -> None:
    """BUY 10@100, BUY 10@120, then SELL 5@150 → realized=200, qty=15, avg=110."""
    await svc.create_transaction(db, _tx(qty=10, price=100, date="2025-01-01"))
    await svc.create_transaction(db, _tx(qty=10, price=120, date="2025-01-02"))
    await svc.create_transaction(db, _tx(qty=-5, price=150, date="2025-01-03"))
    p = await _only_position(db)

    assert p["quantity"] == Decimal("15")
    assert p["avg_cost"] == Decimal("110")
    assert p["cost_basis"] == Decimal("1650")           # 15 × 110
    assert p["last_price"] == Decimal("150")
    assert p["current_value"] == Decimal("2250")        # 15 × 150
    assert p["unrealized_pnl"] == Decimal("600")        # 2250 − 1650
    # 5 × (150 − 110) = 200
    assert p["realized_pnl"] == Decimal("200")
    assert p["transactions_count"] == 3


# ─── Scenario D — partial sell with fee on the sell ─────────────────────────


@pytest.mark.integration
async def test_scenario_d_partial_sell_with_fee(db) -> None:
    """Same as C but SELL 5@150 fee $2 → realized=198 (200 minus 2 fee)."""
    await svc.create_transaction(db, _tx(qty=10, price=100, date="2025-01-01"))
    await svc.create_transaction(db, _tx(qty=10, price=120, date="2025-01-02"))
    await svc.create_transaction(db, _tx(qty=-5, price=150, fee=2, date="2025-01-03"))
    p = await _only_position(db)

    # 5 × 150 − 5 × 110 − 2 = 750 − 550 − 2 = 198
    assert p["realized_pnl"] == Decimal("198")
    # Position unchanged in terms of qty/avg_cost vs scenario C
    assert p["quantity"] == Decimal("15")
    assert p["avg_cost"] == Decimal("110")
    assert p["cost_basis"] == Decimal("1650")


# ─── Scenario E — fully closed position ─────────────────────────────────────


@pytest.mark.integration
async def test_scenario_e_closed_position(db) -> None:
    """BUY 10@100, SELL 10@150 → realized=500, qty=0, avg_cost=None, cost=0."""
    await svc.create_transaction(db, _tx(qty=10, price=100, date="2025-01-01"))
    await svc.create_transaction(db, _tx(qty=-10, price=150, date="2025-01-02"))
    p = await _only_position(db)

    assert p["quantity"] == Decimal("0")
    assert p["avg_cost"] is None
    assert p["cost_basis"] == Decimal("0")
    assert p["current_value"] is None
    assert p["unrealized_pnl"] is None
    assert p["realized_pnl"] == Decimal("500")
    # last_price suppressed to None when quantity == 0
    assert p["last_price"] is None
    assert p["transactions_count"] == 2


# ─── Input validation ───────────────────────────────────────────────────────


@pytest.mark.unit
def test_qty_zero_rejected_by_pydantic() -> None:
    with pytest.raises(ValueError, match="non-zero"):
        TransactionCreate(
            symbol="AAPL",
            currency="USD",
            date=date_type(2025, 1, 1),
            qty=Decimal("0"),
            price=Decimal("100"),
            fee=Decimal("0"),
        )


@pytest.mark.unit
def test_price_zero_rejected_by_pydantic() -> None:
    with pytest.raises(ValueError):
        TransactionCreate(
            symbol="AAPL",
            currency="USD",
            date=date_type(2025, 1, 1),
            qty=Decimal("10"),
            price=Decimal("0"),
            fee=Decimal("0"),
        )


@pytest.mark.unit
def test_negative_fee_rejected_by_pydantic() -> None:
    with pytest.raises(ValueError):
        TransactionCreate(
            symbol="AAPL",
            currency="USD",
            date=date_type(2025, 1, 1),
            qty=Decimal("10"),
            price=Decimal("100"),
            fee=Decimal("-1"),
        )


@pytest.mark.integration
async def test_non_usd_currency_rejected_by_service(db) -> None:
    with pytest.raises(svc.UnsupportedCurrencyError, match="Multi-currency"):
        await svc.create_transaction(
            db, _tx(qty=10, price=100, currency="EUR"),
        )

    # No position should have been created
    rows = (await db.execute(select(Position))).scalars().all()
    assert rows == []


@pytest.mark.integration
async def test_oversell_rejected_by_service(db) -> None:
    await svc.create_transaction(db, _tx(qty=10, price=100, date="2025-01-01"))
    with pytest.raises(svc.OversellError, match="only 10"):
        await svc.create_transaction(
            db, _tx(qty=-15, price=120, date="2025-01-02"),
        )

    # Position still has the original BUY only
    p = await _only_position(db)
    assert p["quantity"] == Decimal("10")
    assert p["transactions_count"] == 1


# ─── DELETE cascade ─────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_delete_last_transaction_cascades_position(db) -> None:
    tx = await svc.create_transaction(db, _tx(qty=10, price=100))
    await svc.delete_transaction(db, tx.id)

    positions = (await db.execute(select(Position))).scalars().all()
    transactions = (await db.execute(select(Transaction))).scalars().all()
    assert positions == []
    assert transactions == []


@pytest.mark.integration
async def test_delete_keeps_position_if_other_transactions_remain(db) -> None:
    first = await svc.create_transaction(db, _tx(qty=10, price=100, date="2025-01-01"))
    await svc.create_transaction(db, _tx(qty=5, price=110, date="2025-01-02"))
    await svc.delete_transaction(db, first.id)

    positions = (await db.execute(select(Position))).scalars().all()
    assert len(positions) == 1

    p = await _only_position(db)
    assert p["quantity"] == Decimal("5")
    assert p["transactions_count"] == 1


@pytest.mark.integration
async def test_delete_unknown_transaction_raises(db) -> None:
    with pytest.raises(svc.TransactionNotFound):
        await svc.delete_transaction(db, uuid.uuid4())


# ─── Empty portfolio ────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_empty_portfolio_summary(db) -> None:
    s = await svc.get_summary(db)
    assert s.total_invested == Decimal("0")
    assert s.current_value == Decimal("0")
    assert s.unrealized_pnl == Decimal("0")
    assert s.realized_pnl == Decimal("0")
    assert s.total_pnl == Decimal("0")
    assert s.pnl_pct is None
    assert s.n_positions == 0
    assert s.n_transactions == 0
    assert s.currency == "USD"


@pytest.mark.integration
async def test_empty_portfolio_lists(db) -> None:
    assert await svc.list_positions(db) == []
    assert await svc.list_transactions(db) == []


# ─── Multi-position summary ─────────────────────────────────────────────────


@pytest.mark.integration
async def test_multi_position_summary_aggregates_correctly(db) -> None:
    # AAPL: BUY 10@100 → cost=1000, value=1000, no realized
    await svc.create_transaction(db, _tx(qty=10, price=100, symbol="AAPL"))
    # MSFT: BUY 5@400 → cost=2000, value=2000, no realized
    await svc.create_transaction(db, _tx(qty=5, price=400, symbol="MSFT"))
    # NVDA: BUY 2@200, SELL 1@300 → cost=200, realized=100, current_value=300
    await svc.create_transaction(
        db, _tx(qty=2, price=200, symbol="NVDA", date="2025-01-01"),
    )
    await svc.create_transaction(
        db, _tx(qty=-1, price=300, symbol="NVDA", date="2025-01-02"),
    )

    s = await svc.get_summary(db)
    # total_invested = 1000 + 2000 + 200 = 3200
    assert s.total_invested == Decimal("3200")
    # current_value = 1000 + 2000 + 300 = 3300
    assert s.current_value == Decimal("3300")
    # unrealized = 3300 - 3200 = 100
    assert s.unrealized_pnl == Decimal("100")
    # realized = 100 (NVDA only)
    assert s.realized_pnl == Decimal("100")
    # total_pnl = 200
    assert s.total_pnl == Decimal("200")
    # pnl_pct = 200 / 3200 = 0.0625
    assert s.pnl_pct == Decimal("0.0625")
    # 3 open positions
    assert s.n_positions == 3
    # 4 total transactions
    assert s.n_transactions == 4
