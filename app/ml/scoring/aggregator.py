"""
Scoring Aggregator — combines all sub-scores into a composite investment signal.

Weights:
  Piotroski  25%  (financial health / earnings quality)
  Altman     20%  (solvency / bankruptcy risk)
  Valuation  20%  (price vs intrinsic value)
  Momentum   15%  (technical / price trend)
  Growth     20%  (revenue, EPS, FCF trajectory)

Verdicts (composite 0-100):
  ≥ 76 → FORT SIGNAL
  ≥ 61 → INTÉRESSANT
  ≥ 41 → NEUTRE
  <  41 → ÉVITER

Halal gate: non-compliant companies get composite=0 and verdict="NON CONFORME".
"""

from typing import Optional

from app.core.logging import logger
from app.ml.scoring.data_fetcher import fetch_scoring_data
from app.ml.scoring.halal_gate import compute_halal_gate
from app.ml.scoring.piotroski import compute_piotroski
from app.ml.scoring.altman import compute_altman
from app.ml.scoring.valuation import compute_valuation
from app.ml.scoring.momentum import compute_momentum
from app.ml.scoring.growth import compute_growth

_WEIGHTS = {
    "piotroski": 0.25,
    "altman": 0.20,
    "valuation": 0.20,
    "momentum": 0.15,
    "growth": 0.20,
}


def _verdict(score: float, halal_compliant: bool) -> str:
    if not halal_compliant:
        return "NON CONFORME"
    if score >= 76:
        return "FORT SIGNAL"
    if score >= 61:
        return "INTÉRESSANT"
    if score >= 41:
        return "NEUTRE"
    return "ÉVITER"


async def compute_full_score(symbol: str) -> dict:
    """
    Compute the full investment score for a ticker.

    Fetches all required data (with 6h cache), runs all sub-scorers,
    applies halal gate, and returns the composite result.

    Returns:
        {
            "symbol": str,
            "composite_score": float (0-100),
            "verdict": str,
            "halal": { "compliant": bool, "reason": str|None, ... },
            "sub_scores": {
                "piotroski": { "score": int, "normalized": float, "criteria": {...} },
                "altman": { "z_score": float, "zone": str, "normalized": float, ... },
                "valuation": { "graham_number": float|None, ..., "score": float },
                "momentum": { "rsi_14": float|None, ..., "score": float },
                "growth": { "revenue_cagr_pct": float|None, ..., "score": float },
            },
            "data_quality": {
                "piotroski_available": bool,
                "altman_available": bool,
                "valuation_available": bool,
                "momentum_available": bool,
                "growth_available": bool,
            },
        }
    """
    symbol = symbol.upper().strip()
    data = await fetch_scoring_data(symbol)

    # ── Halal gate ────────────────────────────────────────────────
    halal = compute_halal_gate(data)

    if not halal["compliant"]:
        logger.info("scoring/%s: halal gate FAILED — %s", symbol, halal["reason"])
        return {
            "symbol": symbol,
            "composite_score": 0.0,
            "verdict": "NON CONFORME",
            "halal": halal,
            "sub_scores": {},
            "data_quality": {
                "piotroski_available": False,
                "altman_available": False,
                "valuation_available": False,
                "momentum_available": False,
                "growth_available": False,
            },
        }

    # ── Sub-scores ────────────────────────────────────────────────
    piotroski = compute_piotroski(data)
    altman = compute_altman(data)
    valuation = compute_valuation(data)
    momentum = compute_momentum(data)
    growth = compute_growth(data)

    p_norm = piotroski["normalized"]
    a_norm = altman["normalized"]
    v_norm = valuation["score"]
    m_norm = momentum["score"]
    g_norm = growth["score"]

    composite = (
        _WEIGHTS["piotroski"] * p_norm
        + _WEIGHTS["altman"] * a_norm
        + _WEIGHTS["valuation"] * v_norm
        + _WEIGHTS["momentum"] * m_norm
        + _WEIGHTS["growth"] * g_norm
    )
    composite = round(min(100.0, max(0.0, composite)), 1)

    verdict = _verdict(composite, halal["compliant"])

    logger.info(
        "scoring/%s: composite=%.1f verdict=%s "
        "(piotroski=%.1f altman=%.1f valuation=%.1f momentum=%.1f growth=%.1f)",
        symbol, composite, verdict,
        p_norm, a_norm, v_norm, m_norm, g_norm,
    )

    return {
        "symbol": symbol,
        "composite_score": composite,
        "verdict": verdict,
        "halal": halal,
        "sub_scores": {
            "piotroski": piotroski,
            "altman": altman,
            "valuation": valuation,
            "momentum": momentum,
            "growth": growth,
        },
        "data_quality": {
            "piotroski_available": piotroski["available"],
            "altman_available": altman["available"],
            "valuation_available": valuation["available"],
            "momentum_available": momentum["available"],
            "growth_available": growth["available"],
        },
    }
