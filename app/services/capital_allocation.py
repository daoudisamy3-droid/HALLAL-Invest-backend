"""Capital allocation — quality component D (Step 4).

Spec authority: §4.2.2 component D (15 % weight). Two equally-weighted
signals after the v2.1 refactor (D2 dividend was removed):

  D1 — Buybacks responsables
       Shares outstanding decreased ≥ 5 % over 3 years AND total
       buyback outflows ≤ cumulative FCF over the same period.
       Tiers: 100 / 60 / 60 / 20 (per spec snippet)

  D3 — Intensité d'investissement maîtrisée
       (CapEx + R&D) / Revenue ratio.
       V1 fallback (Q4 plan): absolute thresholds when no sector
       median is available.

Final component score = mean of available sub-scores (None excluded).
Returns None if both sub-scores are None.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from app.services import financials_service as fs
from app.services.sector import SectorInfo


_CAGR_WINDOW_YEARS = 3  # buyback / FCF accumulation window


@dataclass(frozen=True)
class CapitalAllocationResult:
    score: float | None
    d1_buybacks: int | None
    d3_investment_intensity: int | None
    details: dict[str, Any]
    available: bool


def compute(
    facts: dict[str, Any],
    sector_info: SectorInfo,
) -> CapitalAllocationResult:
    d1, d1_details = _compute_d1(facts)
    d3, d3_details = _compute_d3(facts, sector_info)

    available_scores = [s for s in (d1, d3) if s is not None]
    if not available_scores:
        return CapitalAllocationResult(
            score=None,
            d1_buybacks=d1,
            d3_investment_intensity=d3,
            details={"d1": d1_details, "d3": d3_details},
            available=False,
        )

    score = round(sum(available_scores) / len(available_scores), 1)
    return CapitalAllocationResult(
        score=score,
        d1_buybacks=d1,
        d3_investment_intensity=d3,
        details={"d1": d1_details, "d3": d3_details},
        available=True,
    )


# ─── D1 — Buybacks responsables ──────────────────────────────────────────────


def _compute_d1(facts: dict[str, Any]) -> tuple[int | None, dict[str, Any]]:
    """Spec snippet §4.2.2 D1, adapted to SEC data.

    Two checks combined:
      a) Shares outstanding decreased ≥ 5 % over 3 years.
      b) Cumulative buyback outflows ≤ cumulative FCF over the same window.

    Score tiers:
      100 — reduction ≥ 5 % AND FCF covers buybacks
       60 — reduction ≥ 5 % but financed by debt (FCF doesn't cover)
       60 — neutral: 0 ≤ reduction < 5 %
       20 — dilution: reduction < 0
       None — required data missing
    """
    shares_series = fs.extract_n_year_annuals(facts, "shares_outstanding", n=_CAGR_WINDOW_YEARS + 1)
    if len(shares_series) < _CAGR_WINDOW_YEARS + 1:
        return None, {"reason": "insufficient shares_outstanding history (< 4 FY entries)"}

    shares_now = shares_series[0][1]
    shares_3y_ago = shares_series[_CAGR_WINDOW_YEARS][1]
    if shares_3y_ago == 0:
        return None, {"reason": "shares_3y_ago is zero"}
    reduction_pct = (shares_3y_ago - shares_now) / shares_3y_ago

    # Sum FCF over the latest 3 FY entries (y0, y-1, y-2).
    ocf_series = fs.extract_n_year_annuals(facts, "operating_cash_flow", n=_CAGR_WINDOW_YEARS)
    capex_series = fs.extract_n_year_annuals(facts, "capex", n=_CAGR_WINDOW_YEARS)
    buyback_series = fs.extract_n_year_annuals(facts, "buybacks", n=_CAGR_WINDOW_YEARS)

    if len(ocf_series) < _CAGR_WINDOW_YEARS:
        return None, {"reason": "insufficient OCF history (< 3 FY entries)"}

    capex_by_end = {d: v for d, v in capex_series}
    fcf_total = sum(
        (ocf - capex_by_end.get(d, Decimal("0")) for d, ocf in ocf_series),
        Decimal("0"),
    )
    buyback_total = sum(
        (v for _, v in buyback_series),
        Decimal("0"),
    )

    fcf_covers_buybacks = fcf_total >= buyback_total

    details: dict[str, Any] = {
        "shares_now": str(shares_now),
        "shares_3y_ago": str(shares_3y_ago),
        "reduction_pct": str(reduction_pct),
        "fcf_3y_total": str(fcf_total),
        "buyback_3y_total": str(buyback_total),
        "fcf_covers_buybacks": fcf_covers_buybacks,
    }

    if reduction_pct >= Decimal("0.05") and fcf_covers_buybacks:
        return 100, details
    if reduction_pct >= Decimal("0.05") and not fcf_covers_buybacks:
        return 60, details
    if reduction_pct >= Decimal("0"):
        return 60, details
    return 20, details


# ─── D3 — Investment intensity ───────────────────────────────────────────────


def _compute_d3(
    facts: dict[str, Any],
    sector_info: SectorInfo,  # noqa: ARG001 — kept for future sector-relative scoring
) -> tuple[int | None, dict[str, Any]]:
    """Spec §4.2.2 D3 with V1 fallback (Q4 plan): absolute thresholds.

    Investment intensity = (CapEx + R&D) / Revenue, computed on the latest
    annual values. Sector median comparison is deferred until a sector-data
    integration lands (étape 5+).

    Absolute fallback thresholds (per spec):
      0.05 ≤ ii ≤ 0.30  →  80  (zone normale)
      ii < 0.05         →  50  (peu d'investissement)
      ii > 0.30         →  60  (très intensif)
      None              →  None if Revenue absent/zero
    """
    capex = fs.extract_latest_annual_value(facts, "capex") or Decimal("0")
    rd = fs.extract_latest_annual_value(facts, "r_and_d") or Decimal("0")
    revenue = fs.extract_latest_annual_value(facts, "revenues")

    if revenue is None or revenue == 0:
        return None, {"reason": "revenue absent or zero"}

    investment = capex + rd
    intensity = investment / revenue

    details: dict[str, Any] = {
        "capex": str(capex),
        "r_and_d": str(rd),
        "revenue": str(revenue),
        "intensity_pct": str(intensity),
        "mode": "absolute_fallback",  # V1: no sector median
    }

    if Decimal("0.05") <= intensity <= Decimal("0.30"):
        return 80, details
    if intensity < Decimal("0.05"):
        return 50, details
    return 60, details
