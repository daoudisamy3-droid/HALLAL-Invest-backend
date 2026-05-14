"""Schemas for Step 8 Phase C — Calendar / Management / Holders.

All three endpoints are thin wrappers around YFinance: they always
return 200 with an ``available`` flag — never 5xx — so the frontend
can render either the data or a clear "données indisponibles" state.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ─── Calendar ──────────────────────────────────────────────────────────────


class EarningsEvent(BaseModel):
    """One row of historical EPS estimate vs actual."""
    model_config = ConfigDict(extra="allow")

    quarter: str | None = None
    eps_estimate: float | None = None
    eps_actual: float | None = None
    surprise_percent: float | None = None


class DividendEvent(BaseModel):
    """One past dividend distribution."""
    model_config = ConfigDict(extra="allow")

    date: str | None = None
    amount: float | None = None


class CalendarReport(BaseModel):
    symbol: str
    available: bool
    next_earnings_date: str | None = None
    next_dividend_date: str | None = None
    ex_dividend_date: str | None = None
    dividend_yield: float | None = None
    earnings_history: list[dict[str, Any]] = Field(default_factory=list)
    dividends: list[dict[str, Any]] = Field(default_factory=list)
    reason: str | None = None
    source: str = "yfinance"


# ─── Management ────────────────────────────────────────────────────────────


class Officer(BaseModel):
    model_config = ConfigDict(extra="allow")

    name: str | None = None
    title: str | None = None
    age: int | None = None
    year_born: int | None = None
    total_pay: Any | None = None
    exercised_value: Any | None = None
    unexercised_value: Any | None = None
    fiscal_year: int | None = None


class ManagementReport(BaseModel):
    symbol: str
    available: bool
    officers: list[Officer] = Field(default_factory=list)
    reason: str | None = None
    source: str = "yfinance"


# ─── Holders ───────────────────────────────────────────────────────────────


class HoldersReport(BaseModel):
    symbol: str
    available: bool
    major_holders: list[dict[str, Any]] = Field(
        default_factory=list,
        description=(
            "Aggregate breakdown from yfinance .major_holders (% institutional, "
            "% insider, # institutions). Shape varies by Yahoo version — we "
            "pass through as records."
        ),
    )
    institutional_holders: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Top 10 13F institutional holders.",
    )
    insider_transactions: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Recent insider transactions from yfinance .insider_transactions.",
    )
    reason: str | None = None
    source: str = "yfinance"
