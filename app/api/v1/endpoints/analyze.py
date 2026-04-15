"""
Analyze endpoints – ANALYZE tab.

Phase 1: /analyze/{ticker}/risk   – raw fundamentals contract.
Phase 2: /analyze/{ticker}/peers  – smart peer engine + recommendation.
         /analyze/compare/chart   – 12-month base-100 compare chart.

All routes degrade gracefully: they never return HTTP 500. Fetch
errors collapse to a neutral shell so the frontend can always render.
"""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.models.schemas import (
    CompareChartResponse,
    SmartPeersResponse,
)
from app.services.analyze_service import _empty_payload, get_risk_core
from app.services.peer_service import get_compare_chart, get_smart_peers

router = APIRouter()


def _validate_symbol(symbol: str) -> str:
    symbol = symbol.upper().strip()
    if not symbol:
        raise HTTPException(status_code=400, detail="Missing ticker symbol")
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")
    return symbol


@router.get(
    "/analyze/{ticker}/risk",
    summary="Risk Core Engine – raw fundamentals",
    description=(
        "Returns the strict 3-block contract (valuation / health / growth) "
        "used by the frontend Risk Scorecard algorithm. Data source: "
        "yfinance (SSOT). 60-second in-memory cache. Missing fields are "
        "returned as null (never 0). Always responds HTTP 200: any fetch "
        "error collapses to the fallback payload so the frontend gauges "
        "can render a neutral baseline."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def analyze_risk(ticker: str) -> dict:
    ticker = _validate_symbol(ticker)
    try:
        return await get_risk_core(ticker)
    except Exception as e:
        print(f"Erreur fetch {ticker}: {e}")
        logger.error("risk route: fatal error for %s: %s", ticker, e)
        return _empty_payload()


# ── Phase 2 – Abyssal Arena ──────────────────────────────────────


# IMPORTANT: the compare-chart literal route must be registered
# BEFORE the catch-all /analyze/{ticker}/peers so FastAPI matches
# /analyze/compare/chart as a literal, not as ticker="compare".


@router.get(
    "/analyze/compare/chart",
    response_model=CompareChartResponse,
    summary="Base-100 compare chart (12 months)",
    description=(
        "Returns 12 months of daily closes for two tickers, rebased "
        "to 100 at the first available price. Empty series on fetch "
        "failure – never 500. 5-minute cache per pair."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def analyze_compare_chart(
    ticker1: str = Query(..., description="First ticker symbol"),
    ticker2: str = Query(..., description="Second ticker symbol"),
) -> CompareChartResponse:
    t1 = _validate_symbol(ticker1)
    t2 = _validate_symbol(ticker2)
    try:
        data = await get_compare_chart(t1, t2)
        return CompareChartResponse(**data)
    except Exception as e:
        print(f"Erreur compare {t1} vs {t2}: {e}")
        logger.error("compare_chart: fatal error for %s vs %s: %s", t1, t2, e)
        return CompareChartResponse(
            ticker1={"symbol": t1, "name": None, "series": []},
            ticker2={"symbol": t2, "name": None, "series": []},
        )


@router.get(
    "/analyze/{ticker}/peers",
    response_model=SmartPeersResponse,
    summary="Smart Peer Engine – top 3 sector peers + recommendation",
    description=(
        "Identifies the top 3 curated sector peers, fetches their Risk "
        "Core metrics in parallel, computes a backend-side 0-100 "
        "risk_score for each, and designates the peer with the best "
        "risk/valuation balance as the recommendation. Aggressive "
        "5-minute cache. Always 200: a crash collapses to the source "
        "ticker's payload with an empty peers list."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def analyze_peers(ticker: str) -> SmartPeersResponse:
    ticker = _validate_symbol(ticker)
    try:
        data = await get_smart_peers(ticker)
        return SmartPeersResponse(**data)
    except Exception as e:
        print(f"Erreur peers {ticker}: {e}")
        logger.error("peers route: fatal error for %s: %s", ticker, e)
        return SmartPeersResponse(ticker=ticker)
