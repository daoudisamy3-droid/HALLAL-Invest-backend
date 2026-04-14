"""
Analyze endpoints – Phase 1 (Risk Core Engine).

Single route that ships the raw fundamentals contract consumed by the
frontend's scoring algorithm. 60s TTL cache, never 500.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.models.schemas import RiskCore
from app.services.analyze_service import get_risk_core

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
    response_model=RiskCore,
    summary="Risk Core Engine – raw fundamentals",
    description=(
        "Returns the strict 3-block contract (valuation / health / growth) "
        "used by the frontend Risk Scorecard algorithm. Data source: "
        "yfinance (SSOT). 60-second in-memory cache. Missing fields are "
        "returned as null, never 0."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def analyze_risk(ticker: str) -> RiskCore:
    ticker = _validate_symbol(ticker)
    try:
        data = await get_risk_core(ticker)
    except Exception as exc:
        logger.error("risk core failed for %s: %s", ticker, exc)
        # Never propagate 500 — return empty contract shell
        return RiskCore()
    return RiskCore(**data)
