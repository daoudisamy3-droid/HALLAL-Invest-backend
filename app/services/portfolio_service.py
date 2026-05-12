"""Portfolio service — positions, transactions, derived P&L (Step 3 part 1).

Spec authority:
  - §11.2 — DB schema (positions / transactions) created in alembic 001.
  - §8 — Portfolio Tracker UI target.
  - master-prompt §1 — service layer.

All arithmetic uses :class:`decimal.Decimal` end-to-end. Floats are
banned from this module — P&L is the most critical user-facing number,
and a float rounding artefact would be catastrophic for the project's
credibility.

Tie-break rule for same-date transactions: sort by ``(date ASC, id ASC)``.
UUIDv4 ``id`` is not chronological, so same-date sequencing is stable
but not strictly trade-order-preserving. Acceptable for V1: users
rarely place multiple trades the same day; if they do, results are
deterministic for a given DB state.

Currency rule (Q6 plan): the API technically accepts any 3-char code,
but this service **rejects** non-USD with a domain error. The endpoint
maps this to HTTP 400. Multi-currency lands in étape 5.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integration.yfinance_client import YFinanceClient
from app.models import Position, Transaction
from app.schemas.portfolio import (
    PortfolioSummary,
    PositionRead,
    TransactionCreate,
    TransactionRead,
)
from app.services import yfinance_service

logger = logging.getLogger(__name__)


ZERO = Decimal("0")
SUPPORTED_CURRENCY = "USD"


# ─── Domain exceptions ──────────────────────────────────────────────────────


class PortfolioError(Exception):
    """Base class for portfolio domain errors."""


class UnsupportedCurrencyError(PortfolioError):
    """A non-USD currency was submitted in V1 (multi-currency = étape 5)."""


class OversellError(PortfolioError):
    """A SELL transaction would push the position quantity below zero."""


class TransactionNotFound(PortfolioError):
    """DELETE on a non-existent transaction id."""


# ─── Public API ─────────────────────────────────────────────────────────────


async def create_transaction(
    db: AsyncSession, payload: TransactionCreate
) -> TransactionRead:
    """Insert a transaction, auto-creating its position if needed.

    Raises:
        UnsupportedCurrencyError: when ``payload.currency != "USD"``.
        OversellError: when the resulting position would be negative.
    """
    if payload.currency != SUPPORTED_CURRENCY:
        raise UnsupportedCurrencyError(
            "Multi-currency portfolio not supported in V1. Only USD positions "
            "accepted (étape 5 will add multi-currency support)."
        )

    position = await _find_or_create_position(db, payload.symbol, payload.currency)

    # Oversell guard — sum existing qty, refuse if adding payload.qty goes negative.
    if payload.qty < ZERO:
        current_qty = await _running_quantity(db, position.id)
        if current_qty + payload.qty < ZERO:
            raise OversellError(
                f"Cannot sell {(-payload.qty)} of {payload.symbol}: "
                f"only {current_qty} held."
            )

    tx = Transaction(
        id=uuid.uuid4(),
        position_id=position.id,
        date=payload.date,
        qty=payload.qty,
        price=payload.price,
        fee=payload.fee,
    )
    db.add(tx)
    await db.commit()
    await db.refresh(tx)

    return TransactionRead(
        id=tx.id,
        position_id=position.id,
        symbol=position.symbol,
        currency=position.currency,
        date=tx.date,
        qty=tx.qty,
        price=tx.price,
        fee=tx.fee,
    )


async def list_transactions(db: AsyncSession) -> list[TransactionRead]:
    """Return all transactions across all positions, chronological order."""
    stmt = (
        select(Transaction, Position)
        .join(Position, Transaction.position_id == Position.id)
        .order_by(Transaction.date, Transaction.id)
    )
    rows = (await db.execute(stmt)).all()
    return [
        TransactionRead(
            id=tx.id,
            position_id=tx.position_id,
            symbol=pos.symbol,
            currency=pos.currency,
            date=tx.date,
            qty=tx.qty,
            price=tx.price,
            fee=tx.fee,
        )
        for tx, pos in rows
    ]


async def list_positions(
    db: AsyncSession,
    *,
    yfinance_client: YFinanceClient | None = None,
) -> list[PositionRead]:
    """List every position (open and closed) with derived fields.

    ``yfinance_client``: when provided, ``last_price`` is sourced from a
    live Yahoo quote (with cache TTL 1h via ``yfinance_service``). On any
    YFinance failure we fall back to the last transaction price — this
    is the V1 stub behavior and remains the default when no client is
    wired (Step 3 callers + service-level tests).
    """
    stmt = select(Position).order_by(Position.opened_at, Position.id)
    positions = (await db.execute(stmt)).scalars().all()

    reports: list[PositionRead] = []
    for pos in positions:
        reports.append(await _enrich_position(db, pos, yfinance_client))
    return reports


async def get_summary(
    db: AsyncSession,
    *,
    yfinance_client: YFinanceClient | None = None,
) -> PortfolioSummary:
    """Aggregate snapshot. Cohérent avec PositionRead (USD-only V1)."""
    positions = await list_positions(db, yfinance_client=yfinance_client)

    total_invested = sum((p.cost_basis for p in positions), ZERO)
    current_value = sum((p.current_value or ZERO for p in positions), ZERO)
    unrealized_pnl = sum((p.unrealized_pnl or ZERO for p in positions), ZERO)
    realized_pnl = sum((p.realized_pnl for p in positions), ZERO)
    total_pnl = unrealized_pnl + realized_pnl
    pnl_pct: Decimal | None = (
        total_pnl / total_invested if total_invested > ZERO else None
    )

    n_positions = sum(1 for p in positions if p.quantity > ZERO)
    n_transactions = sum(p.transactions_count for p in positions)

    return PortfolioSummary(
        total_invested=total_invested,
        current_value=current_value,
        unrealized_pnl=unrealized_pnl,
        realized_pnl=realized_pnl,
        total_pnl=total_pnl,
        pnl_pct=pnl_pct,
        n_positions=n_positions,
        n_transactions=n_transactions,
        currency=SUPPORTED_CURRENCY,
    )


async def delete_transaction(db: AsyncSession, tx_id: uuid.UUID) -> None:
    """Delete a transaction. Cascade: drop the position if no tx remains."""
    tx = await db.get(Transaction, tx_id)
    if tx is None:
        raise TransactionNotFound(str(tx_id))

    position_id = tx.position_id
    await db.delete(tx)
    await db.commit()

    # Cascade: if the position has no more transactions, drop the position too.
    remaining = await db.execute(
        select(func.count()).select_from(Transaction).where(
            Transaction.position_id == position_id
        )
    )
    if int(remaining.scalar_one() or 0) == 0:
        await db.execute(sa_delete(Position).where(Position.id == position_id))
        await db.commit()


# ─── Internals ──────────────────────────────────────────────────────────────


async def _find_or_create_position(
    db: AsyncSession, symbol: str, currency: str
) -> Position:
    stmt = (
        select(Position)
        .where(Position.symbol == symbol)
        .where(Position.currency == currency)
        .limit(1)
    )
    existing = (await db.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        return existing

    pos = Position(
        id=uuid.uuid4(),
        symbol=symbol,
        currency=currency,
        opened_at=datetime.now(tz=timezone.utc),
    )
    db.add(pos)
    await db.commit()
    await db.refresh(pos)
    return pos


async def _running_quantity(db: AsyncSession, position_id: uuid.UUID) -> Decimal:
    """Sum of all transaction qty for a position (signed)."""
    stmt = (
        select(func.coalesce(func.sum(Transaction.qty), 0))
        .where(Transaction.position_id == position_id)
    )
    raw: Any = (await db.execute(stmt)).scalar_one()
    return Decimal(str(raw if raw is not None else 0))


async def _enrich_position(
    db: AsyncSession,
    position: Position,
    yfinance_client: YFinanceClient | None,
) -> PositionRead:
    """Replay the position's ledger to derive qty / avg_cost / P&L.

    Algorithm (weighted-average cost, Q3 plan):

      for tx ordered by (date ASC, id ASC):
        if tx.qty > 0 (BUY):
            running_cost += tx.qty * tx.price + tx.fee
            running_qty  += tx.qty
        else (SELL, qty < 0):
            sell_qty     = -tx.qty
            avg_at_sell  = running_cost / running_qty
            cost_removed = avg_at_sell * sell_qty
            running_cost -= cost_removed
            running_qty  -= sell_qty
            realized_pnl += sell_qty * tx.price - cost_removed - tx.fee
        last_price = tx.price

    Decimal exact arithmetic — no float anywhere.
    """
    stmt = (
        select(Transaction)
        .where(Transaction.position_id == position.id)
        .order_by(Transaction.date, Transaction.id)
    )
    txs = (await db.execute(stmt)).scalars().all()

    running_qty = ZERO
    running_cost = ZERO  # cost basis of currently held shares (fees included)
    realized_pnl = ZERO
    last_price: Decimal | None = None

    for tx in txs:
        if tx.qty > ZERO:
            running_cost += tx.qty * tx.price + tx.fee
            running_qty += tx.qty
        else:
            sell_qty = -tx.qty
            if running_qty > ZERO:
                avg_at_sell = running_cost / running_qty
            else:
                # Should never happen given oversell guard — defensive only.
                avg_at_sell = ZERO
            cost_removed = avg_at_sell * sell_qty
            running_cost -= cost_removed
            running_qty -= sell_qty
            realized_pnl += sell_qty * tx.price - cost_removed - tx.fee
        last_price = tx.price

    avg_cost: Decimal | None = None
    if running_qty > ZERO:
        avg_cost = running_cost / running_qty

    # Resolve the price + its source. Live > transaction fallback > unavailable.
    resolved_price: Decimal | None = None
    price_source: str = "transaction"
    if running_qty > ZERO and yfinance_client is not None:
        live = await _fetch_live_price(db, position.symbol, yfinance_client)
        if live is not None:
            resolved_price = live
            price_source = "live"
    if resolved_price is None:
        # Fall back to last transaction price (the pre-step-5 stub behavior).
        if last_price is not None and running_qty > ZERO:
            resolved_price = last_price
            price_source = "transaction"
        else:
            price_source = "unavailable"

    current_value: Decimal | None = None
    unrealized_pnl: Decimal | None = None
    if resolved_price is not None and running_qty > ZERO:
        current_value = running_qty * resolved_price
        unrealized_pnl = current_value - running_cost

    return PositionRead(
        id=position.id,
        symbol=position.symbol,
        currency=position.currency,
        opened_at=position.opened_at,
        quantity=running_qty,
        avg_cost=avg_cost,
        cost_basis=running_cost,
        last_price=resolved_price,
        price_source=price_source,  # type: ignore[arg-type]
        current_value=current_value,
        unrealized_pnl=unrealized_pnl,
        realized_pnl=realized_pnl,
        transactions_count=len(txs),
    )


async def _fetch_live_price(
    db: AsyncSession,
    symbol: str,
    client: YFinanceClient,
) -> Decimal | None:
    """Pull a live quote from YFinance, return ``None`` on any failure.

    The yfinance ``.info`` dict has shifted over time; we try a few price
    keys in priority order and validate the result is a positive number.
    """
    info = await yfinance_service.get_info(symbol, db, client)
    if not isinstance(info, dict):
        return None
    for key in ("regularMarketPrice", "currentPrice", "previousClose"):
        raw = info.get(key)
        if raw is None:
            continue
        try:
            price = Decimal(str(raw))
        except Exception:
            continue
        if price > ZERO:
            return price
    return None
