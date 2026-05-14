"""GET /api/v1/{calendar,management,holders}/{symbol} — Step 8 Phase C.

Three new YFinance-backed tabs. Each endpoint always returns 200 with
an ``available`` flag in the body (never 5xx on Yahoo flakiness).
"""

from typing import Any

from fastapi import APIRouter, Depends, Path

from app.core.database import AsyncSessionLocal
from app.integration.yfinance_client import YFinanceClient, get_yfinance_client
from app.schemas.yfinance_tabs import (
    CalendarReport,
    HoldersReport,
    ManagementReport,
    Officer,
)
from app.services import yfinance_service


calendar_router = APIRouter(prefix="/calendar", tags=["calendar"])
management_router = APIRouter(prefix="/management", tags=["management"])
holders_router = APIRouter(prefix="/holders", tags=["holders"])


async def _db_session():
    async with AsyncSessionLocal() as session:
        yield session


# ─── /calendar ──────────────────────────────────────────────────────────────


@calendar_router.get("/{symbol}", response_model=CalendarReport)
async def calendar(
    symbol: str = Path(..., min_length=1, max_length=20),
    db=Depends(_db_session),
    yfinance: YFinanceClient = Depends(get_yfinance_client),
) -> CalendarReport:
    sym = symbol.strip().upper()
    payload = await yfinance_service.get_calendar(sym, db, yfinance)
    if not isinstance(payload, dict):
        return CalendarReport(
            symbol=sym,
            available=False,
            reason="YFinance n'a renvoyé aucune donnée calendrier (rate limit ou ticker non couvert).",
        )

    return CalendarReport(
        symbol=sym,
        available=True,
        next_earnings_date=_pick_date(payload, "Earnings Date", "earningsDate", "earnings_date"),
        next_dividend_date=_pick_date(payload, "Dividend Date", "dividendDate", "dividend_date"),
        ex_dividend_date=_pick_date(payload, "Ex-Dividend Date", "exDividendDate", "ex_dividend_date"),
        dividend_yield=_pick_number(payload, "dividend_yield", "dividendYield"),
        earnings_history=_safe_list(payload.get("earnings_history")),
        dividends=_safe_list(payload.get("dividends")),
    )


# ─── /management ────────────────────────────────────────────────────────────


@management_router.get("/{symbol}", response_model=ManagementReport)
async def management(
    symbol: str = Path(..., min_length=1, max_length=20),
    db=Depends(_db_session),
    yfinance: YFinanceClient = Depends(get_yfinance_client),
) -> ManagementReport:
    sym = symbol.strip().upper()
    payload = await yfinance_service.get_management(sym, db, yfinance)
    if not isinstance(payload, list) or not payload:
        return ManagementReport(
            symbol=sym,
            available=False,
            reason="YFinance n'a renvoyé aucun dirigeant pour ce ticker.",
        )

    officers = [Officer(**(o if isinstance(o, dict) else {})) for o in payload]
    return ManagementReport(symbol=sym, available=True, officers=officers)


# ─── /holders ───────────────────────────────────────────────────────────────


@holders_router.get("/{symbol}", response_model=HoldersReport)
async def holders(
    symbol: str = Path(..., min_length=1, max_length=20),
    db=Depends(_db_session),
    yfinance: YFinanceClient = Depends(get_yfinance_client),
) -> HoldersReport:
    sym = symbol.strip().upper()
    payload = await yfinance_service.get_holders(sym, db, yfinance)
    if not isinstance(payload, dict):
        return HoldersReport(
            symbol=sym,
            available=False,
            reason="YFinance n'a renvoyé aucune donnée actionnaires.",
        )

    return HoldersReport(
        symbol=sym,
        available=True,
        major_holders=_safe_list(payload.get("major_holders")),
        institutional_holders=_safe_list(payload.get("institutional_holders")),
        insider_transactions=_safe_list(payload.get("insider_transactions")),
    )


# ─── Helpers ────────────────────────────────────────────────────────────────


def _pick_date(payload: dict[str, Any], *keys: str) -> str | None:
    for k in keys:
        v = payload.get(k)
        if isinstance(v, str) and v:
            return v
        if isinstance(v, list) and v and isinstance(v[0], str):
            return v[0]
    return None


def _pick_number(payload: dict[str, Any], *keys: str) -> float | None:
    for k in keys:
        v = payload.get(k)
        if isinstance(v, (int, float)) and v == v:  # NaN-safe
            return float(v)
    return None


def _safe_list(v: Any) -> list[dict[str, Any]]:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    return []
