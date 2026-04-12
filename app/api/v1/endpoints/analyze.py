"""
Analyze endpoints – Risk / Earnings / Peers.

All routes use the in-service 60s TTL cache. Never return 500: missing
data surfaces as null fields and descriptive flags in the payload.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.models.schemas import (
    EarningsCalendar,
    PeersAnalysis,
    RiskAnalysis,
)
from app.services.analyze_service import (
    get_earnings_calendar,
    get_peers_analysis,
    get_risk_analysis,
)

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
    response_model=RiskAnalysis,
    summary="Risk score engine (0-100)",
    description=(
        "Deterministic risk score from yfinance fundamentals. "
        "Weights: 35% Valuation (P/E vs sector), 35% Health (Net margin + ROE), "
        "30% Growth (earnings or revenue growth). Returns total_score and "
        "the 3 sub-scores. Missing data → null, never 500. 60s cache."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def analyze_risk(ticker: str) -> RiskAnalysis:
    ticker = _validate_symbol(ticker)
    try:
        data = await get_risk_analysis(ticker)
    except Exception as exc:
        logger.error("risk analysis failed for %s: %s", ticker, exc)
        # Never propagate 500 — return an empty shell
        return RiskAnalysis(symbol=ticker, flags=["INTERNAL_ERROR"])
    return RiskAnalysis(**data)


@router.get(
    "/analyze/{ticker}/earnings",
    response_model=EarningsCalendar,
    summary="Upcoming earnings & consensus EPS",
    description=(
        "Next earnings date, consensus EPS (mean/low/high) and revenue "
        "estimate from yfinance calendar. Includes an empty "
        "`what_to_watch` array (LLM hook reserved). 60s cache."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def analyze_earnings(ticker: str) -> EarningsCalendar:
    ticker = _validate_symbol(ticker)
    try:
        data = await get_earnings_calendar(ticker)
    except Exception as exc:
        logger.error("earnings fetch failed for %s: %s", ticker, exc)
        return EarningsCalendar(symbol=ticker, flags=["INTERNAL_ERROR"])
    return EarningsCalendar(**data)


@router.get(
    "/analyze/{ticker}/peers",
    response_model=PeersAnalysis,
    summary="Sector peers + top 3 alternatives",
    description=(
        "Curated sector peer universe scored by a ROE/PE composite "
        "(60% ROE, 40% valuation). Returns the full peer list plus the "
        "top 3 alternatives (highest composite score). 60s cache."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def analyze_peers(ticker: str) -> PeersAnalysis:
    ticker = _validate_symbol(ticker)
    try:
        data = await get_peers_analysis(ticker)
    except Exception as exc:
        logger.error("peers fetch failed for %s: %s", ticker, exc)
        return PeersAnalysis(symbol=ticker, flags=["INTERNAL_ERROR"])
    return PeersAnalysis(**data)
