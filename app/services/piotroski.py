"""Piotroski F-Score — quality component A (Step 4).

Spec authority: §4.2.2 component A (30 % weight). The 9 standard
Piotroski (2000) criteria are well-known and not enumerated in the
spec text; we implement the canonical set below.

Standard 9 criteria (1 point each):

  Profitability
    1.  NetIncome > 0
    2.  ROA = NetIncome / TotalAssets > 0
    3.  OperatingCashFlow > 0
    4.  CashFlowQuality: OCF > NetIncome

  Leverage / Liquidity / Funding
    5.  ΔLongTermDebt < 0       (long-term debt decreased YoY)
    6.  ΔCurrentRatio > 0       (current ratio improved YoY)
    7.  No new shares issued     (shares_y0 ≤ shares_y1, tolerance 1 %)

  Operating efficiency
    8.  ΔGrossMargin > 0        (gross profit / sales improved YoY)
    9.  ΔAssetTurnover > 0      (sales / total assets improved YoY)

Per spec §4.2.2:
  - If N (calculable criteria) < 6 → component returns ``None`` (the
    weighted-quality aggregator will redistribute the 30 % to other
    components).
  - Otherwise the score is ``(raw / N) × 100``, NOT ``raw / 9 × 100``.
    This way a partial Piotroski (e.g. share count missing) is
    fair-graded over the criteria actually evaluated.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any

from app.services import financials_service as fs


# Tolerance on "no new shares issued" — accounts for trivial dilution
# from RSU vesting / option exercises (not equity raises).
_SHARES_TOLERANCE = Decimal("1.01")  # ≤ 1 % growth still counts as "no issuance"


_PIOTROSKI_FORMULA = "F-Score = sum(criteria passed) ; score = (raw / N evaluated) × 100"
_PIOTROSKI_THRESHOLDS = [
    {"label": "STRONG",  "condition": "score ≥ 78 (i.e. ≥ 7/9)"},
    {"label": "AVERAGE", "condition": "55 ≤ score < 78"},
    {"label": "WEAK",    "condition": "score < 55"},
    {"label": "N/A",     "condition": "N evaluated < 6 (insufficient YoY data)"},
]

_CRITERION_DESCRIPTIONS: dict[str, str] = {
    "c1_net_income_positive":      "NetIncome > 0",
    "c2_roa_positive":              "NetIncome / TotalAssets > 0",
    "c3_ocf_positive":              "OperatingCashFlow > 0",
    "c4_ocf_gt_ni":                 "OCF > NetIncome (earnings quality)",
    "c5_ltd_decreased":             "ΔLongTermDebt < 0 (deleveraging)",
    "c6_current_ratio_improved":   "ΔCurrentRatio > 0",
    "c7_no_share_issuance":         "shares_y0 ≤ shares_y1 × 1.01 (no equity raise)",
    "c8_gross_margin_improved":    "ΔGrossMargin > 0",
    "c9_asset_turnover_improved":   "ΔAssetTurnover > 0",
}


@dataclass(frozen=True)
class PiotroskiResult:
    score: float | None             # normalised 0-100 (None if N < 6)
    raw: int | None                 # criteria-passed count (None if N=0)
    n_evaluated: int                # criteria actually evaluable
    criteria: dict[str, bool | None]  # per-criterion result
    available: bool                 # True iff score is not None
    calculation_detail: dict[str, Any] = field(default_factory=dict)


def compute(facts: dict[str, Any]) -> PiotroskiResult:
    """Compute F-Score from a SEC company_facts payload (2 years required)."""
    ni_series = fs.extract_n_year_annuals(facts, "net_income", n=2)
    ta_series = fs.extract_n_year_annuals(facts, "total_assets", n=2)
    ocf_series = fs.extract_n_year_annuals(facts, "operating_cash_flow", n=2)
    ltd_series = fs.extract_n_year_annuals(facts, "long_term_debt", n=2)
    ca_series = fs.extract_n_year_annuals(facts, "current_assets", n=2)
    cl_series = fs.extract_n_year_annuals(facts, "current_liabilities", n=2)
    shares_series = fs.extract_n_year_annuals(facts, "shares_outstanding", n=2)
    gp_series = fs.extract_n_year_annuals(facts, "gross_profit", n=2)
    rev_series = fs.extract_n_year_annuals(facts, "revenues", n=2)

    # Latest-year shorthands (None if no FY entry at all).
    ni0 = _y0(ni_series)
    ta0 = _y0(ta_series)
    ocf0 = _y0(ocf_series)

    # Previous-year shorthands (None if only 1 year of data).
    ni1 = _y1(ni_series)  # noqa: F841 (kept for symmetry / future criteria)
    ta1 = _y1(ta_series)
    ltd0, ltd1 = _y0(ltd_series), _y1(ltd_series)
    ca0, ca1 = _y0(ca_series), _y1(ca_series)
    cl0, cl1 = _y0(cl_series), _y1(cl_series)
    sh0, sh1 = _y0(shares_series), _y1(shares_series)
    gp0, gp1 = _y0(gp_series), _y1(gp_series)
    rev0, rev1 = _y0(rev_series), _y1(rev_series)

    criteria: dict[str, bool | None] = {}

    # 1. Net Income > 0
    criteria["c1_net_income_positive"] = (ni0 > 0) if ni0 is not None else None

    # 2. ROA > 0 (uses latest TA)
    if ni0 is not None and ta0 is not None and ta0 != 0:
        criteria["c2_roa_positive"] = (ni0 / ta0) > 0
    else:
        criteria["c2_roa_positive"] = None

    # 3. OCF > 0
    criteria["c3_ocf_positive"] = (ocf0 > 0) if ocf0 is not None else None

    # 4. OCF > NI (cash flow quality)
    if ocf0 is not None and ni0 is not None:
        criteria["c4_ocf_gt_ni"] = ocf0 > ni0
    else:
        criteria["c4_ocf_gt_ni"] = None

    # 5. ΔLongTermDebt < 0
    if ltd0 is not None and ltd1 is not None:
        criteria["c5_ltd_decreased"] = ltd0 < ltd1
    else:
        criteria["c5_ltd_decreased"] = None

    # 6. ΔCurrentRatio > 0
    if (
        ca0 is not None and cl0 is not None and cl0 != 0
        and ca1 is not None and cl1 is not None and cl1 != 0
    ):
        criteria["c6_current_ratio_improved"] = (ca0 / cl0) > (ca1 / cl1)
    else:
        criteria["c6_current_ratio_improved"] = None

    # 7. No new shares issued (tolerance 1 %)
    if sh0 is not None and sh1 is not None and sh1 != 0:
        criteria["c7_no_share_issuance"] = sh0 <= sh1 * _SHARES_TOLERANCE
    else:
        criteria["c7_no_share_issuance"] = None

    # 8. ΔGrossMargin > 0
    if (
        gp0 is not None and rev0 is not None and rev0 != 0
        and gp1 is not None and rev1 is not None and rev1 != 0
    ):
        criteria["c8_gross_margin_improved"] = (gp0 / rev0) > (gp1 / rev1)
    else:
        criteria["c8_gross_margin_improved"] = None

    # 9. ΔAssetTurnover > 0
    if (
        rev0 is not None and ta0 is not None and ta0 != 0
        and rev1 is not None and ta1 is not None and ta1 != 0
    ):
        criteria["c9_asset_turnover_improved"] = (rev0 / ta0) > (rev1 / ta1)
    else:
        criteria["c9_asset_turnover_improved"] = None

    evaluated = [v for v in criteria.values() if v is not None]
    n_eval = len(evaluated)

    def _build_detail(score_val: float | None, raw_val: int | None) -> dict[str, Any]:
        # The "variables" map shows each criterion's verbal description.
        variables = {key: _CRITERION_DESCRIPTIONS.get(key, key)
                     for key in criteria.keys()}
        # The "intermediates" map shows each criterion's outcome (pass / fail / na).
        intermediates = {
            key: ("✓ pass" if v is True else ("✗ fail" if v is False else "N/A"))
            for key, v in criteria.items()
        }
        if score_val is None:
            interp = (
                f"Seulement {n_eval}/9 critères évaluables (< 6 minimum). "
                "Composante Piotroski reportée à None ; les 30 % de pondération "
                "sont redistribués sur les autres composantes."
            )
            steps: list[str] = [
                f"Critères évaluables = {n_eval}",
                "n_eval < 6 → score indisponible",
            ]
        else:
            interp = (
                f"raw = {raw_val}/{n_eval} critères passés → "
                f"score = ({raw_val}/{n_eval}) × 100 = {score_val}"
            )
            steps = [
                f"Critères évaluables : {n_eval}/9",
                f"Critères passés     : {raw_val}/{n_eval}",
                f"score = ({raw_val} / {n_eval}) × 100 = {score_val}",
            ]
        return {
            "formula": _PIOTROSKI_FORMULA,
            "variables": variables,
            "intermediates": intermediates,
            "computation_steps": steps,
            "result": str(score_val) if score_val is not None else None,
            "thresholds": _PIOTROSKI_THRESHOLDS,
            "interpretation": interp,
        }

    if n_eval < 6:
        return PiotroskiResult(
            score=None,
            raw=None,
            n_evaluated=n_eval,
            criteria=criteria,
            available=False,
            calculation_detail=_build_detail(None, None),
        )

    raw = sum(1 for v in evaluated if v)
    # Spec §4.2.2: `raw / N × 100` when N < 9, else `raw / 9 × 100` (same form).
    score = round((raw / n_eval) * 100, 1)

    return PiotroskiResult(
        score=score,
        raw=raw,
        n_evaluated=n_eval,
        criteria=criteria,
        available=True,
        calculation_detail=_build_detail(score, raw),
    )


# ─── Helpers ────────────────────────────────────────────────────────────────


def _y0(series: list[tuple[date, Decimal]]) -> Decimal | None:
    """Most-recent year value, or None if empty."""
    return series[0][1] if series else None


def _y1(series: list[tuple[date, Decimal]]) -> Decimal | None:
    """Previous-year value (index 1), or None if not enough history."""
    return series[1][1] if len(series) >= 2 else None
