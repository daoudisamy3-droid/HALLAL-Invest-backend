"""
Calendar Service — earnings schedule + dividend intel, no HTTP concerns.

Called by:
  - GET /calendar/{symbol}  (calendar endpoint wraps with HTTPException)
  - GET /plan/{symbol}      (plan endpoint calls directly)
"""

from datetime import date

from app.core.cache import cache_get, cache_set
from app.core.logging import logger
from app.integration.yfinance_client import get_calendar


_CALENDAR_NAMESPACE = "calendar"
_CALENDAR_TTL = 3600  # 1 hour


async def compute_calendar(symbol: str) -> dict:
    """
    Core calendar logic.
    Raises RuntimeError if the underlying fetch fails.
    """
    cached = cache_get(_CALENDAR_NAMESPACE, symbol)
    if cached is not None:
        logger.info("calendar_service/%s: cache hit", symbol)
        return cached

    data = await get_calendar(symbol)

    # ── Earnings next date(s) ─────────────────────────────────────
    earnings_dates_list: list[str] = data.get("earnings_dates_list", [])
    today = date.today()

    next_date: str | None = None
    next_date_to: str | None = None
    confirmed: bool = False
    days_until: int | None = None
    proximity_warning: bool = False

    if len(earnings_dates_list) == 1:
        next_date = earnings_dates_list[0]
        confirmed = True
    elif len(earnings_dates_list) >= 2:
        sorted_dates = sorted(earnings_dates_list)
        next_date = sorted_dates[0]
        next_date_to = sorted_dates[-1]
        confirmed = False

    if next_date:
        try:
            next_date_obj = date.fromisoformat(next_date)
            days_until = (next_date_obj - today).days
            proximity_warning = 0 <= days_until <= 7
        except Exception:
            pass

    # ── Dividend ──────────────────────────────────────────────────
    div_yield_raw = data.get("div_yield")
    if div_yield_raw is not None:
        div_yield_pct = round(float(div_yield_raw) * 100, 2)
        if div_yield_pct > 50:
            div_yield_pct = round(div_yield_pct / 100, 2)
    else:
        div_yield_pct = None

    result = {
        "symbol": symbol,
        "earnings": {
            "next_date": next_date,
            "next_date_to": next_date_to,
            "confirmed": confirmed,
            "days_until": days_until,
            "proximity_warning": proximity_warning,
        },
        "dividend": {
            "has_dividend": data.get("has_dividend", False),
            "ex_date": data.get("ex_div_iso"),
            "entry_deadline": data.get("entry_deadline_iso"),
            "payment_date": data.get("div_pay_iso"),
            "annual_rate": data.get("div_rate"),
            "yield_pct": div_yield_pct,
            "adr_disclaimer": data.get("is_adr", False),
        },
        "earnings_history": data.get("history", []),
        "is_adr": data.get("is_adr", False),
    }

    cache_set(_CALENDAR_NAMESPACE, symbol, result, ttl=_CALENDAR_TTL)
    logger.info(
        "calendar_service/%s: next_earnings=%s confirmed=%s has_div=%s history=%d entries",
        symbol, next_date, confirmed,
        result["dividend"]["has_dividend"],
        len(result["earnings_history"]),
    )
    return result
