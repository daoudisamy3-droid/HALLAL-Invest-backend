"""
Calendar Endpoint — Earnings schedule + dividend intel for any ticker.

Route:
    GET /calendar/{symbol}

Data sources (all via yfinance, no new integrations):
  - ticker.calendar    → next earnings date(s)
  - ticker.info        → dividend dates, rate, yield
  - ticker.earnings_dates → last 4 quarters of EPS history

Cache: TTL 1h (earnings calendars update infrequently).
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.services.calendar_service import compute_calendar


router = APIRouter()


@router.get(
    "/calendar/{symbol}",
    summary="Earnings & Dividend Calendar",
    description=(
        "Returns the next earnings date(s), dividend schedule, last 4 quarters "
        "of EPS history, and a proximity warning when earnings are within 7 days."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def calendar(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    try:
        return await compute_calendar(symbol)
    except Exception as exc:
        logger.error("calendar/%s: %s", symbol, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch calendar data for '{symbol}'. Please retry later.",
        )
