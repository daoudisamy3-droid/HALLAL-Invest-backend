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
assert abs(sum(_WEIGHTS.values()) - 1.0) < 1e-9, (
    f"_WEIGHTS must sum to 1.0, got {sum(_WEIGHTS.values()):.6f}"
)


def _safe_compute(name: str, fn, data: dict, symbol: str = "") -> dict:
    try:
        return fn(data)
    except Exception as exc:
        logger.exception("scoring/%s: %s failed: %s", symbol, name, exc)
        return {
            "score": 50.0,
            "normalized": 50.0,
            "available": False,
            "error": str(exc),
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
                "growth": { "revenue_growth_pct": float|None, ..., "score": float },
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
    piotroski = _safe_compute("piotroski", compute_piotroski, data, symbol)
    altman    = _safe_compute("altman",    compute_altman,    data, symbol)
    valuation = _safe_compute("valuation", compute_valuation, data, symbol)
    momentum  = _safe_compute("momentum",  compute_momentum,  data, symbol)
    growth    = _safe_compute("growth",    compute_growth,    data, symbol)

    p_norm = piotroski.get("normalized", 50.0)
    a_norm = altman.get("normalized", 50.0)
    v_norm = valuation.get("score", 50.0)
    m_norm = momentum.get("score", 50.0)
    g_norm = growth.get("score", 50.0)

    # Dynamic reweighting — exclude unavailable components
    components = {
        "piotroski": (p_norm, _WEIGHTS["piotroski"], piotroski.get("available", True)),
        "altman":    (a_norm, _WEIGHTS["altman"],    altman.get("available", True)),
        "valuation": (v_norm, _WEIGHTS["valuation"], valuation.get("available", True)),
        "momentum":  (m_norm, _WEIGHTS["momentum"],  momentum.get("available", True)),
        "growth":    (g_norm, _WEIGHTS["growth"],    growth.get("available", True)),
    }
    available_components = [(s, w) for s, w, avail in components.values() if avail]

    if not available_components:
        composite = 50.0
    else:
        total_w = sum(w for _, w in available_components)
        composite = sum(s * w for s, w in available_components) / total_w

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
