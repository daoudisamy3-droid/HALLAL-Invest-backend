"""Multi-year growth — quality component B (Step 4).

Spec authority: §4.2.2 component B (25 % weight). Three sub-metrics
averaged:

  - Revenue CAGR 3y
  - EPS (diluted) CAGR 3y
  - FCF CAGR 3y  (FCF = OperatingCashFlow − CapEx, V1 V-shape)

Per-metric scoring (spec table verbatim):

      CAGR ≥ 15%  →  100
      CAGR ≥ 10%  →   80
      CAGR ≥  5%  →   60
      CAGR ≥  0%  →   40
      CAGR ≥ -5%  →   20
      else         →    0

Final score = mean of the available metrics (None metrics are excluded
from the mean rather than treated as 0 — same pattern as the quality
aggregator).

Decimal-safe CAGR formula::

    CAGR = (end / start) ** (1 / years) − 1

Decimal's ``**`` operator does NOT accept a Decimal exponent. We use
``Decimal.ln()`` / ``Decimal.exp()`` for fractional powers, which gives
exact precision under the active context (default 28 digits — plenty).

Negative-base guard: if ``end / start`` is non-positive (e.g. went
from positive to negative net income), the CAGR is undefined; we
return ``None`` for that metric and let the aggregator skip it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, getcontext
from typing import Any

from app.services import financials_service as fs


_CAGR_YEARS = 3  # 3-year CAGR per spec
_MIN_YEARS_REQUIRED = _CAGR_YEARS + 1  # need 4 annual entries (y-3 → y0)

# Boundaries — values are the *lower* end (inclusive) of each tier.
_TIERS: tuple[tuple[Decimal, int], ...] = (
    (Decimal("0.15"), 100),
    (Decimal("0.10"),  80),
    (Decimal("0.05"),  60),
    (Decimal("0"),     40),
    (Decimal("-0.05"), 20),
)


@dataclass(frozen=True)
class GrowthResult:
    score: float | None              # mean of available sub-scores, None if 0 available
    raw_cagrs: dict[str, Decimal | None]
    sub_scores: dict[str, int | None]
    available: bool


def compute(facts: dict[str, Any]) -> GrowthResult:
    """Compute the growth component score from SEC company_facts."""
    rev_cagr = _cagr_for_concept(facts, "revenues")
    eps_cagr = _cagr_for_concept(facts, "eps_diluted")
    fcf_cagr = _fcf_cagr(facts)

    raw = {
        "revenue_cagr_3y": rev_cagr,
        "eps_cagr_3y": eps_cagr,
        "fcf_cagr_3y": fcf_cagr,
    }
    sub = {k: _score_cagr(v) for k, v in raw.items()}

    available_sub_scores = [s for s in sub.values() if s is not None]
    if not available_sub_scores:
        return GrowthResult(
            score=None, raw_cagrs=raw, sub_scores=sub, available=False
        )

    score = round(sum(available_sub_scores) / len(available_sub_scores), 1)
    return GrowthResult(score=score, raw_cagrs=raw, sub_scores=sub, available=True)


# ─── Internals ──────────────────────────────────────────────────────────────


def _cagr_for_concept(facts: dict[str, Any], concept: str) -> Decimal | None:
    """3-year CAGR for a single SEC concept, or None if not computable."""
    series = fs.extract_n_year_annuals(facts, concept, n=_MIN_YEARS_REQUIRED)
    if len(series) < _MIN_YEARS_REQUIRED:
        return None
    # series is most-recent-first → end = series[0], start = series[3]
    end_val = series[0][1]
    start_val = series[_CAGR_YEARS][1]
    return _cagr(start_val, end_val, _CAGR_YEARS)


def _fcf_cagr(facts: dict[str, Any]) -> Decimal | None:
    """FCF = OperatingCashFlow − CapEx, computed per-year then CAGR'd.

    CapEx in SEC is reported as a *cash outflow* (positive number). FCF =
    OCF − CapEx (subtraction, not addition).
    """
    ocf_series = fs.extract_n_year_annuals(facts, "operating_cash_flow", n=_MIN_YEARS_REQUIRED)
    capex_series = fs.extract_n_year_annuals(facts, "capex", n=_MIN_YEARS_REQUIRED)
    if len(ocf_series) < _MIN_YEARS_REQUIRED:
        return None

    # Align by period_end — both series are independently ordered by `end` desc;
    # we rely on annual reporting being on consistent fiscal year-ends.
    capex_by_end = {d: v for d, v in capex_series}
    fcfs: list[tuple[date, Decimal]] = []
    for d, ocf in ocf_series:
        capex = capex_by_end.get(d)
        if capex is None:
            # Spec §3.2.2 says CapEx may be absent (zero capex companies).
            # Treat as zero — FCF = OCF in that case.
            capex = Decimal("0")
        fcfs.append((d, ocf - capex))

    if len(fcfs) < _MIN_YEARS_REQUIRED:
        return None
    end_val = fcfs[0][1]
    start_val = fcfs[_CAGR_YEARS][1]
    return _cagr(start_val, end_val, _CAGR_YEARS)


def _cagr(start: Decimal, end: Decimal, years: int) -> Decimal | None:
    """Compound annual growth rate, Decimal-safe.

    ``CAGR = (end/start)^(1/years) − 1``.

    Returns ``None`` if start/end have invalid signs (CAGR undefined
    when crossing zero) or if the resulting expression is non-positive.
    """
    if start is None or end is None:
        return None
    if start == 0 or end == 0:
        return None
    # Same sign required (CAGR undefined for sign-changing series).
    if (start > 0) != (end > 0):
        return None

    ratio = end / start
    if ratio <= 0:
        return None

    # Decimal-safe fractional power via ln/exp under the active context.
    # Set local precision high enough that the rounded CAGR is stable.
    ctx = getcontext()
    original_prec = ctx.prec
    try:
        ctx.prec = max(original_prec, 50)
        cagr = (ratio.ln() / Decimal(years)).exp() - Decimal(1)
    finally:
        ctx.prec = original_prec

    return cagr


def _score_cagr(cagr: Decimal | None) -> int | None:
    """Map a CAGR Decimal to the discrete 0-100 score per spec §4.2.2."""
    if cagr is None:
        return None
    for lower, score in _TIERS:
        if cagr >= lower:
            return score
    return 0
