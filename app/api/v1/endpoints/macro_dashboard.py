"""
Macro Dashboard Endpoint.

Route:
    GET /macro/dashboard

Returns market regime (RISK-ON/NEUTRE/RISK-OFF), FRED macro data,
global market tickers, and upcoming economic calendar events.

Cache TTL: 15 min.
"""

from fastapi import APIRouter, Depends

from app.core.logging import logger
from app.core.security import rate_limit_dependency

router = APIRouter()


@router.get(
    "/macro/dashboard",
    summary="Macro Dashboard",
    description=(
        "Régime de marché global (RISK-ON/NEUTRE/RISK-OFF), données FRED, "
        "marchés globaux (indices, commodités, FX, taux), "
        "et calendrier des événements économiques majeurs. Cache TTL 15 min."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_macro_dashboard() -> dict:
    from app.ml.scoring.macro_engine import fetch_full_macro

    try:
        return await fetch_full_macro()
    except Exception as exc:
        logger.error("macro_dashboard: %s", exc)
        return {
            "regime": {
                "regime": "NEUTRE",
                "regime_color": "amber",
                "score": 50,
                "factors": [],
                "warnings": [],
                "regime_note": "Données temporairement indisponibles",
            },
            "fred": {},
            "markets": {},
            "calendar": [],
        }
