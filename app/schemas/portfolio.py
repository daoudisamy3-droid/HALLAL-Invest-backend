"""Pydantic schemas for the portfolio endpoints (Step 3 part 1).

Spec authority:
  - §11.2 — positions(id, symbol, currency, opened_at) +
    transactions(id, position_id, date, qty, price, fee). Derived fields
    (avg_cost, current_value, P&L) are computed on the fly, never
    stored — see ``app/services/portfolio_service.py``.
  - master-prompt §1 mapping (extended with `portfolio_service.py`).

Sign convention for ``qty`` (Q2 plan validated): **signed**.
  - ``qty > 0`` → BUY
  - ``qty < 0`` → SELL
  - ``qty == 0`` → rejected (422)

V1 single-currency (Q6 plan validated): the API technically accepts any
3-char ISO 4217 string in ``currency`` so the OpenAPI schema stays
generic for future multi-currency support, but the service layer
**rejects** any non-USD value with a 400 + explicit message. Multi-currency
arrives in étape 5.

``last_price`` documentation note (Q1 plan validated): in the absence
of a real-time price source in V1, ``last_price`` is the **proxy** of
the most recent transaction price for the position. This is NOT a
market quote. To be replaced by a YFinance (or equivalent) feed in
étape 5.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TransactionCreate(BaseModel):
    """Body of POST /api/v1/portfolio/transactions.

    Validation:
      - symbol non-empty, ≤ 20 chars, auto-uppercased.
      - currency 3 chars, auto-uppercased. Service rejects ≠ "USD" in V1.
      - date is the trade date (accept future dates for V1 — scheduled-buy
        use-case, no validation).
      - qty signed, non-zero (positive=BUY, negative=SELL).
      - price strictly positive.
      - fee non-negative.
    """

    model_config = ConfigDict(extra="forbid")

    symbol: Annotated[str, Field(min_length=1, max_length=20)]
    currency: Annotated[str, Field(min_length=3, max_length=3)] = "USD"
    date: date
    qty: Decimal
    price: Annotated[Decimal, Field(gt=Decimal("0"))]
    fee: Annotated[Decimal, Field(ge=Decimal("0"))] = Decimal("0")

    @field_validator("symbol")
    @classmethod
    def _symbol_upper(cls, v: str) -> str:
        out = v.strip().upper()
        if not out:
            raise ValueError("symbol cannot be empty")
        return out

    @field_validator("currency")
    @classmethod
    def _currency_upper(cls, v: str) -> str:
        return v.strip().upper()

    @field_validator("qty")
    @classmethod
    def _qty_non_zero(cls, v: Decimal) -> Decimal:
        if v == Decimal("0"):
            raise ValueError(
                "qty must be non-zero (positive=BUY, negative=SELL)"
            )
        return v


class TransactionRead(BaseModel):
    """A persisted transaction row plus joined position metadata."""

    id: UUID
    position_id: UUID
    symbol: str
    currency: str
    date: date
    qty: Decimal
    price: Decimal
    fee: Decimal


class PositionRead(BaseModel):
    """A position with all derived fields computed on the fly.

    All Decimal fields are USD (single-currency in V1). When the position
    is closed (``quantity == 0``), the average-cost-dependent fields
    (``avg_cost``, ``current_value``, ``unrealized_pnl``) become ``None``
    while ``realized_pnl`` keeps the accumulated P&L over the lifetime
    of the position.
    """

    id: UUID
    symbol: str
    currency: str
    opened_at: datetime

    # Derived — recomputed at each read from the transaction ledger.
    quantity: Decimal
    avg_cost: Decimal | None = Field(
        None,
        description=(
            "Weighted-average cost of currently-held shares (fees included). "
            "None when quantity == 0."
        ),
    )
    cost_basis: Decimal = Field(
        ...,
        description="Total cost of currently-held shares = quantity × avg_cost.",
    )
    last_price: Decimal | None = Field(
        None,
        description=(
            "PROXY for the current market price: the most recent transaction "
            "price on this position. NOT a real-time quote. Replaced by a "
            "YFinance/equivalent feed in étape 5."
        ),
    )
    current_value: Decimal | None = Field(
        None,
        description="quantity × last_price (None if no transactions or qty==0).",
    )
    unrealized_pnl: Decimal | None = Field(
        None,
        description="current_value − cost_basis (None if current_value is None).",
    )
    realized_pnl: Decimal = Field(
        ...,
        description="Cumulated profit/loss from SELL transactions on this position.",
    )
    transactions_count: int


class PortfolioSummary(BaseModel):
    """Aggregate snapshot across all positions (USD assumption, V1)."""

    total_invested: Decimal
    current_value: Decimal
    unrealized_pnl: Decimal
    realized_pnl: Decimal
    total_pnl: Decimal
    pnl_pct: Decimal | None = Field(
        None,
        description="(unrealized + realized) / total_invested. None if invested == 0.",
    )
    n_positions: int = Field(..., description="Open positions with quantity > 0.")
    n_transactions: int
    currency: str = "USD"
