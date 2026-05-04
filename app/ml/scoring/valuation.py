"""
Valuation Score — Graham Number, PEG, analyst consensus upside.

Components:
  1. Margin of safety = (graham_number - price) / graham_number * 100
  2. PEG ratio (price / earnings growth)
  3. Analyst upside = (target_mean_price - price) / price * 100

Graham Number = sqrt(22.5 * EPS * BVPS)
  - Only valid when EPS > 0 and BVPS > 0
  - Represents the maximum fair price for a value investor

Composite score: 40% MoS + 30% PEG + 30% analyst upside
"""

import math
from typing import Optional

from app.ml.scoring.normalizer import normalize_margin_of_safety, normalize_peg, normalize_linear


def _normalize_analyst_upside(upside_pct: Optional[float]) -> float:
    """
    Analyst upside % → 0-100.

    > 30% upside → 100
    0-30% → 50-100
    < 0% (downside) → 0-49
    """
    if upside_pct is None:
        return 50.0
    if upside_pct >= 30.0:
        return 100.0
    if upside_pct >= 0.0:
        return 50.0 + upside_pct / 30.0 * 50.0
    return max(0.0, 50.0 + upside_pct / 50.0 * 50.0)


def compute_valuation(data: dict) -> dict:
    """
    Compute valuation score from scoring data dict.

    Returns:
        {
            "graham_number": float | None,
            "margin_of_safety_pct": float | None,
            "peg": float | None,
            "analyst_upside_pct": float | None,
            "score": float (0-100),
            "available": bool,
        }
    """
    current_price: Optional[float] = data.get("current_price")
    eps: Optional[float] = data.get("eps")
    bvps: Optional[float] = data.get("book_value_per_share")
    peg: Optional[float] = data.get("peg_ratio")
    target_mean: Optional[float] = data.get("target_mean_price")

    available = current_price is not None and current_price > 0

    # ── Graham Number ─────────────────────────────────────────────
    graham_number: Optional[float] = None
    margin_of_safety_pct: Optional[float] = None

    if eps is not None and eps > 0 and bvps is not None and bvps > 0:
        graham_number = round(math.sqrt(22.5 * eps * bvps), 2)
        if current_price and current_price > 0:
            margin_of_safety_pct = round(
                (graham_number - current_price) / graham_number * 100.0, 2
            )

    # ── Analyst upside ────────────────────────────────────────────
    analyst_upside_pct: Optional[float] = None
    if target_mean is not None and current_price and current_price > 0:
        analyst_upside_pct = round(
            (target_mean - current_price) / current_price * 100.0, 2
        )

    # ── Component scores (only include calculable components) ─────
    components_val = []

    if margin_of_safety_pct is not None:
        mos_score = normalize_margin_of_safety(margin_of_safety_pct)
        components_val.append((mos_score, 0.40, "mos"))

    if peg is not None and peg > 0:
        peg_score = normalize_peg(peg)
        components_val.append((peg_score, 0.30, "peg"))

    if analyst_upside_pct is not None:
        upside_score = _normalize_analyst_upside(analyst_upside_pct)
        components_val.append((upside_score, 0.30, "upside"))

    if not components_val:
        score = 50.0
        val_available = False
    else:
        total_w = sum(w for _, w, _ in components_val)
        score = sum(s * w for s, w, _ in components_val) / total_w
        val_available = True

    return {
        "graham_number": graham_number,
        "margin_of_safety_pct": margin_of_safety_pct,
        "peg": peg,
        "analyst_upside_pct": analyst_upside_pct,
        "score": round(score, 1),
        "available": val_available,
    }
