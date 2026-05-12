"""Tests for app/services/piotroski.py — F-Score quality component A.

Three canonical scenarios (0/9, 5/9, 9/9) with crafted multi-year data.
The score is normalised as ``(raw / n_evaluated) × 100`` per spec §4.2.2.
"""

from __future__ import annotations

import pytest

from app.services import piotroski
from tests._score_helpers import make_facts


def _two_year_facts(*, y0: dict, y1: dict) -> dict:
    """Build 2 years of GAAP entries from two flat dicts (y0=latest, y1=prior).

    Each dict maps GAAP-tag → numeric value.
    """
    by_tag: dict[str, list[tuple[str, int | float]]] = {}
    for tag, val in y1.items():
        by_tag.setdefault(tag, []).append(("2023-12-31", val))
    for tag, val in y0.items():
        by_tag.setdefault(tag, []).append(("2024-12-31", val))
    return make_facts(by_tag)


@pytest.mark.unit
def test_piotroski_perfect_9_of_9() -> None:
    """All 9 criteria pass → score = 100.0."""
    facts = _two_year_facts(
        y1={
            "NetIncomeLoss":       100,
            "Assets":               1100,
            "NetCashProvidedByUsedInOperatingActivities": 90,
            "LongTermDebt":         500,
            "AssetsCurrent":        250,
            "LiabilitiesCurrent":   200,
            "CommonStockSharesOutstanding": 100,
            "GrossProfit":          300,
            "Revenues":             900,
        },
        y0={
            "NetIncomeLoss":       120,                              # c1 ✓ (>0)
            "Assets":               1000,                            # c2 ✓ (ROA 120/1000>0)
            "NetCashProvidedByUsedInOperatingActivities": 150,       # c3 ✓ (>0), c4 ✓ (OCF>NI)
            "LongTermDebt":         400,                             # c5 ✓ (400<500)
            "AssetsCurrent":        300,
            "LiabilitiesCurrent":   150,                             # c6 ✓ (300/150=2 > 250/200=1.25)
            "CommonStockSharesOutstanding": 100,                     # c7 ✓ (stable)
            "GrossProfit":          400,
            "Revenues":             1000,                            # c8 ✓ (400/1000=0.4 > 300/900=0.333)
                                                                     # c9 ✓ (1000/1000=1.0 > 900/1100=0.818)
        },
    )
    result = piotroski.compute(facts)
    assert result.available is True
    assert result.score == 100.0
    assert result.raw == 9
    assert result.n_evaluated == 9
    assert all(v is True for v in result.criteria.values())


@pytest.mark.unit
def test_piotroski_zero_of_9() -> None:
    """All 9 criteria fail → score = 0.0."""
    facts = _two_year_facts(
        y1={
            "NetIncomeLoss":        20,
            "Assets":                900,
            "NetCashProvidedByUsedInOperatingActivities": 80,
            "LongTermDebt":         400,
            "AssetsCurrent":        300,
            "LiabilitiesCurrent":   200,
            "CommonStockSharesOutstanding": 100,
            "GrossProfit":          200,
            "Revenues":             1000,
        },
        y0={
            "NetIncomeLoss":       -50,                              # c1 fail (NI<0)
            "Assets":               1000,                            # c2 fail (ROA<0)
            "NetCashProvidedByUsedInOperatingActivities": -100,      # c3 fail (OCF<0)
                                                                     # c4 fail (-100 > -50 = False)
            "LongTermDebt":         500,                             # c5 fail (500>400)
            "AssetsCurrent":        200,
            "LiabilitiesCurrent":   200,                             # c6 fail (1.0 > 1.5 = False)
            "CommonStockSharesOutstanding": 110,                     # c7 fail (110 > 100*1.01)
            "GrossProfit":          100,
            "Revenues":             1000,                            # c8 fail (0.1 > 0.2 = False)
                                                                     # c9 fail (1.0 > 1.111 = False)
        },
    )
    result = piotroski.compute(facts)
    assert result.score == 0.0
    assert result.raw == 0
    assert result.n_evaluated == 9


@pytest.mark.unit
def test_piotroski_mixed_5_of_9() -> None:
    """Craft 5 pass, 4 fail criteria deterministically → score = (5/9)*100 ≈ 55.6."""
    facts = _two_year_facts(
        y1={
            "NetIncomeLoss":        50,
            "Assets":                1000,
            "NetCashProvidedByUsedInOperatingActivities": 80,
            "LongTermDebt":         400,
            "AssetsCurrent":        300,
            "LiabilitiesCurrent":   200,
            "CommonStockSharesOutstanding": 100,
            "GrossProfit":          300,
            "Revenues":             1000,
        },
        y0={
            "NetIncomeLoss":       100,                              # c1 ✓
            "Assets":               1000,                            # c2 ✓ (ROA=0.1>0)
            "NetCashProvidedByUsedInOperatingActivities": 50,        # c3 ✓ (>0), c4 fail (50<100)
            "LongTermDebt":         500,                             # c5 fail (increased)
            "AssetsCurrent":        300,
            "LiabilitiesCurrent":   200,                             # c6 fail (1.5 == 1.5, not >)
            "CommonStockSharesOutstanding": 100,                     # c7 ✓ (stable)
            "GrossProfit":          400,
            "Revenues":             1000,                            # c8 ✓ (0.4 > 0.3)
                                                                     # c9 ✓ (1.0 > 1.0 = False)
        },
    )
    result = piotroski.compute(facts)
    # Expected criteria status (verify the count):
    expected_pass = {"c1_net_income_positive", "c2_roa_positive",
                     "c3_ocf_positive", "c7_no_share_issuance",
                     "c8_gross_margin_improved"}
    expected_fail = {"c4_ocf_gt_ni", "c5_ltd_decreased",
                     "c6_current_ratio_improved", "c9_asset_turnover_improved"}
    actual_pass = {k for k, v in result.criteria.items() if v is True}
    actual_fail = {k for k, v in result.criteria.items() if v is False}
    assert actual_pass == expected_pass, f"unexpected pass set: {actual_pass}"
    assert actual_fail == expected_fail, f"unexpected fail set: {actual_fail}"
    assert result.raw == 5
    assert result.n_evaluated == 9
    assert result.score == round((5 / 9) * 100, 1)


@pytest.mark.unit
def test_piotroski_insufficient_data_returns_none() -> None:
    """Fewer than 6 criteria evaluable → score = None, available = False."""
    facts = _two_year_facts(
        y1={"NetIncomeLoss": 100, "Assets": 900},
        y0={"NetIncomeLoss": 120, "Assets": 1000},
        # No OCF, no LTD, no CA/CL, no shares, no GP/Rev → only c1+c2 evaluable.
    )
    result = piotroski.compute(facts)
    assert result.available is False
    assert result.score is None
    assert result.n_evaluated == 2
    assert result.raw is None


@pytest.mark.unit
def test_piotroski_shares_tolerance_1_percent() -> None:
    """Share count grows by 0.5 % → c7 still passes (tolerance 1 %)."""
    facts = _two_year_facts(
        y1={
            "NetIncomeLoss": 100, "Assets": 1000,
            "NetCashProvidedByUsedInOperatingActivities": 120,
            "LongTermDebt": 300,
            "AssetsCurrent": 400, "LiabilitiesCurrent": 200,
            "CommonStockSharesOutstanding": 1_000_000,
            "GrossProfit": 400, "Revenues": 1000,
        },
        y0={
            "NetIncomeLoss": 110, "Assets": 1000,
            "NetCashProvidedByUsedInOperatingActivities": 120,
            "LongTermDebt": 290,
            "AssetsCurrent": 450, "LiabilitiesCurrent": 200,
            "CommonStockSharesOutstanding": 1_005_000,   # +0.5 % (within tolerance)
            "GrossProfit": 450, "Revenues": 1100,
        },
    )
    result = piotroski.compute(facts)
    assert result.criteria["c7_no_share_issuance"] is True
