"""
Growth Score — quality and trajectory of earnings and cash flow.

Components:
  1. Revenue CAGR (YoY growth proxy from 2 annual periods)
  2. EPS CAGR (YoY)
  3. Net margin (net_income / revenue)
  4. FCF quality (operating_cash_flow / net_income)
  5. Beat rate (% of quarters EPS beat estimate, last 4)

Composite: 25% revenue growth + 25% EPS growth + 20% net margin + 20% FCF + 10% beat rate
"""

import math
from typing import Optional

from app.ml.scoring.normalizer import (
    normalize_cagr,
    normalize_fcf_quality,
    normalize_beat_rate,
    normalize_linear,
)


def _yoy_growth(current: Optional[float], previous: Optional[float]) -> Optional[float]:
    """Single-period YoY growth rate as percentage. Returns None if data missing or prev ≤ 0."""
    if current is None or previous is None or previous <= 0:
        return None
    return round((current / previous - 1.0) * 100.0, 2)


def _normalize_net_margin(margin_pct: Optional[float]) -> float:
    """
    Net margin % → 0-100.

    > 20% → excellent → 90-100
    10-20% → good → 70-89
    5-10% → fair → 50-69
    0-5% → thin → 30-49
    < 0% → loss → 0-29
    """
    if margin_pct is None:
        return 50.0
    if margin_pct >= 20.0:
        return min(100.0, 90.0 + (margin_pct - 20.0) * 0.5)
    if margin_pct >= 10.0:
        return 70.0 + (margin_pct - 10.0) * 2.0
    if margin_pct >= 5.0:
        return 50.0 + (margin_pct - 5.0) * 4.0
    if margin_pct >= 0.0:
        return 30.0 + margin_pct * 4.0
    return max(0.0, 30.0 + margin_pct * 1.5)


def compute_growth(data: dict) -> dict:
    """
    Compute growth score from scoring data dict.

    Returns:
        {
            "revenue_growth_pct": float | None,
            "eps_growth_pct": float | None,
            "net_margin_pct": float | None,
            "fcf_quality": float | None,
            "beat_rate_pct": float | None,
            "score": float (0-100),
            "available": bool,
        }
    """
    revenue: Optional[float] = data.get("revenue")
    revenue_prev: Optional[float] = data.get("revenue_prev")
    net_income: Optional[float] = data.get("net_income")
    eps: Optional[float] = data.get("eps")
    eps_prev: Optional[float] = data.get("eps_prev")
    operating_cash_flow: Optional[float] = data.get("operating_cash_flow")
    earnings_beats: Optional[int] = data.get("earnings_beats")
    earnings_total: Optional[int] = data.get("earnings_total")

    available = revenue is not None and net_income is not None

    # ── Revenue growth ────────────────────────────────────────────
    revenue_cagr = _yoy_growth(revenue, revenue_prev)

    # ── EPS growth ────────────────────────────────────────────────
    eps_cagr = _yoy_growth(eps, eps_prev)

    # ── Net margin ────────────────────────────────────────────────
    net_margin_pct: Optional[float] = None
    if revenue and revenue > 0 and net_income is not None:
        net_margin_pct = round(net_income / revenue * 100.0, 2)

    # ── FCF quality ───────────────────────────────────────────────
    fcf_quality: Optional[float] = None
    if operating_cash_flow is not None and net_income and abs(net_income) > 0:
        fcf_quality = round(operating_cash_flow / net_income, 4)

    # ── Beat rate ─────────────────────────────────────────────────
    beat_rate_pct: Optional[float] = None
    if earnings_total and earnings_total > 0:
        beat_rate_pct = round((earnings_beats or 0) / earnings_total * 100.0, 1)

    # ── Component scores ──────────────────────────────────────────
    rev_score = normalize_cagr(revenue_cagr)
    eps_score = normalize_cagr(eps_cagr)
    margin_score = _normalize_net_margin(net_margin_pct)
    fcf_score = normalize_fcf_quality(fcf_quality)
    beat_score = normalize_beat_rate(earnings_beats, earnings_total)

    score = (
        0.25 * rev_score
        + 0.25 * eps_score
        + 0.20 * margin_score
        + 0.20 * fcf_score
        + 0.10 * beat_score
    )

    return {
        "revenue_growth_pct": revenue_cagr,
        "eps_growth_pct": eps_cagr,
        "net_margin_pct": net_margin_pct,
        "fcf_quality": fcf_quality,
        "beat_rate_pct": beat_rate_pct,
        "score": round(score, 1),
        "available": available,
    }
