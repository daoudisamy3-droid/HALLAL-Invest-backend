"""
Analyze endpoints – Phase 1 (Risk Core Engine).

Single route that ships the raw fundamentals contract consumed by the
frontend's scoring algorithm. 60s TTL cache, graceful degradation:
never 500, always HTTP 200 with the strict contract shell.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.services.analyze_service import _empty_payload, get_risk_core

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
        # Absolute safety net. get_risk_core already swallows errors,
        # but we double-guard here so a programmer error in the service
        # layer can never leak a 500 to the frontend.
        print(f"Erreur fetch {ticker}: {e}")
        logger.error("risk route: fatal error for %s: %s", ticker, e)
        return _empty_payload()
