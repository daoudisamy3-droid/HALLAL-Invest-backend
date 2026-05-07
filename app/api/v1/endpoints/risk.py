"""
Risk Intelligence Endpoint — Adaptive risk block for any ticker.

Crosses ML prediction confidence with YFinance fundamentals to produce:
  - Volatility-driven profile (Défensif / Standard / Agressif)
  - Stop-loss and take-profit levels with R/R ratio
  - Kelly position sizing (requires ≥ 5 verified predictions)
  - Conviction score 0-100
  - Contextual insight phrase (template-based, no generative AI)

Route:
    GET /risk/{symbol}
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.services.risk_service import compute_risk


router = APIRouter()


@router.get(
    "/risk/{symbol}",
    summary="Risk Intelligence Block",
    description=(
        "Computes an adaptive risk block for any ticker by crossing ML prediction "
        "confidence with YFinance fundamentals. Returns stop-loss, take-profit, "
        "R/R ratio, Kelly sizing, and a conviction score (0-100)."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def risk_intelligence(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    try:
        return await compute_risk(symbol)
    except Exception as exc:
        logger.error("risk/%s: %s", symbol, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not compute risk block for '{symbol}'. Please retry later.",
        )
