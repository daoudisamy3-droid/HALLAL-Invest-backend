import asyncio

from fastapi import APIRouter, HTTPException, Depends

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.integration import yfinance_client
from app.services.financial_engine import extract_fundamentals, compute_technicals
from app.services.shariah_screening import screen as shariah_screen
from app.services.ai_analysis import get_ai_commentary
from app.models.schemas import TickerResponse, PriceResponse, Technicals

router = APIRouter()


@router.get(
    "/ticker/{symbol}/price",
    response_model=PriceResponse,
    summary="Lightweight price snapshot",
    description="Returns current price, change, and change percentage. No AI, no Shariah, no technicals.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_ticker_price(symbol: str) -> PriceResponse:
    symbol = symbol.upper().strip()
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    try:
        data = await yfinance_client.get_fast_price(symbol)
    except Exception as exc:
        logger.error("Price fetch failed for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch price for '{symbol}'. Verify the symbol is correct.",
        )

    return PriceResponse(**data)


@router.get(
    "/ticker/{symbol}",
    response_model=TickerResponse,
    summary="Full ticker analysis",
    description=(
        "Aggregates raw fundamentals, technicals, and Shariah screening "
        "from financial APIs. Optional AI commentary reads but never modifies the data."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_ticker(symbol: str) -> TickerResponse:
    symbol = symbol.upper().strip()
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    errors: list[str] = []

    # ── Phase 1: Parallel raw data fetching ───────────────────────
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

    # ── Phase 2: Raw data extraction (no AI, no gap-filling) ─────
    fundamentals = extract_fundamentals(info)

    try:
        technicals = compute_technicals(history)
    except Exception as exc:
        logger.warning("Technical analysis failed for %s: %s", symbol, exc)
        errors.append(f"Technical analysis partial failure: {exc}")
        technicals = Technicals(current_price=float(history["Close"].iloc[-1]))

    shariah = shariah_screen(info)

    # ── Phase 3: AI Commentary (read-only, optional, graceful) ────
    ai_commentary = None
    try:
        ai_commentary = await get_ai_commentary(symbol, fundamentals, technicals, shariah)
    except Exception as exc:
        logger.warning("AI commentary failed for %s: %s", symbol, exc)
        errors.append(f"AI commentary unavailable: {exc}")

    return TickerResponse(
        symbol=symbol,
        fundamentals=fundamentals,
        technicals=technicals,
        shariah=shariah,
        ai_commentary=ai_commentary,
        errors=errors,
    )
