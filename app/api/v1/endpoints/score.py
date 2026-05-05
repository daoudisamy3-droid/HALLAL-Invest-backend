"""
Score Endpoint — composite investment signal for any ticker.

Route:
    GET /score/{symbol}

Combines Piotroski F-Score, Altman Z-Score, valuation (Graham/PEG),
momentum (RSI/MA/52w), and growth (CAGR/FCF/beat rate) into a single
0-100 composite with an AAOIFI halal gate.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.ml.scoring.aggregator import compute_full_score

router = APIRouter()


@router.get(
    "/score/{symbol}",
    summary="Score Global d'Analyse",
    description=(
        "Calcule un score global 0-100 pour une action en combinant "
        "Piotroski F-Score (qualité fondamentale), Altman Z-Score "
        "(risque faillite), valorisation Graham/PEG, momentum technique "
        "et croissance des earnings. Inclut un Halal Gate AAOIFI automatique."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_score(symbol: str) -> dict:
    symbol = symbol.upper().strip()

    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    try:
        result = await compute_full_score(symbol)
        return result
    except Exception as exc:
        logger.error("score/%s: failed: %s", symbol, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not compute score for '{symbol}': {str(exc)}",
        )
