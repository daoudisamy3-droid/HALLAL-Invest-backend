import asyncio

from fastapi import APIRouter, HTTPException, Depends

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.integration import yfinance_client
from app.services.financial_engine import compute_fundamentals, compute_technicals
from app.services.shariah_screening import screen as shariah_screen
from app.services.ai_analysis import get_ai_verdict
from app.models.schemas import TickerResponse

router = APIRouter()


@router.get(
    "/ticker/{symbol}",
    response_model=TickerResponse,
    summary="Full ticker analysis",
    description=(
        "Aggregates fundamentals, technicals, Shariah screening, and AI verdict "
        "for a given stock symbol. Parallel fetches for low latency."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_ticker(symbol: str) -> TickerResponse:
    symbol = symbol.upper().strip()
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    errors: list[str] = []

    # ── Phase 1: Parallel data fetching ───────────────────────────
    try:
        info, history = await asyncio.gather(
            yfinance_client.get_ticker_info(symbol),
            yfinance_client.get_history(symbol, period="5y", interval="1d"),
        )
    except Exception as exc:
        logger.error("Data fetch failed for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch data for '{symbol}'. Verify the symbol is correct.",
        )

    # ── Phase 2: CPU-bound computations ───────────────────────────
    fundamentals = compute_fundamentals(info)

    try:
        technicals = compute_technicals(history)
    except Exception as exc:
        logger.warning("Technical analysis failed for %s: %s", symbol, exc)
        errors.append(f"Technical analysis partial failure: {exc}")
        from app.models.schemas import Technicals
        technicals = Technicals(current_price=float(history["Close"].iloc[-1]))

    shariah = await shariah_screen(info, symbol)

    # ── Phase 3: AI Analysis (non-blocking, graceful degradation) ─
    ai_verdict = None
    try:
        ai_verdict = await get_ai_verdict(symbol, fundamentals, technicals, shariah)
    except Exception as exc:
        logger.warning("AI analysis failed for %s: %s", symbol, exc)
        errors.append(f"AI analysis unavailable: {exc}")

    return TickerResponse(
        symbol=symbol,
        fundamentals=fundamentals,
        technicals=technicals,
        shariah=shariah,
        ai_verdict=ai_verdict,
        errors=errors,
    )
