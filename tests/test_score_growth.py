"""Tests for app/services/growth.py — CAGR-driven quality component B."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import growth
from tests._score_helpers import make_facts


@pytest.mark.unit
def test_cagr_exact_10_percent_3_years() -> None:
    """y-3=100, y0=133.1 → CAGR exact 10 %."""
    cagr = growth._cagr(Decimal("100"), Decimal("133.1"), 3)
    assert cagr is not None
    # Allow tiny rounding from ln/exp (50-digit Decimal precision).
    assert abs(cagr - Decimal("0.1")) < Decimal("1e-20")


@pytest.mark.unit
def test_cagr_returns_none_on_sign_change() -> None:
    assert growth._cagr(Decimal("100"), Decimal("-50"), 3) is None
    assert growth._cagr(Decimal("-50"), Decimal("100"), 3) is None
    assert growth._cagr(Decimal("0"), Decimal("100"), 3) is None
    assert growth._cagr(Decimal("100"), Decimal("0"), 3) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "cagr_value, expected_score",
    [
        (Decimal("0.20"), 100),  # >= 15 %
        (Decimal("0.12"),  80),  # 10 to 15 %
        (Decimal("0.07"),  60),  # 5 to 10 %
        (Decimal("0.02"),  40),  # 0 to 5 %
        (Decimal("-0.02"), 20),  # -5 to 0 %
        (Decimal("-0.10"),  0),  # < -5 %
    ],
)
def test_score_cagr_tiers(cagr_value: Decimal, expected_score: int) -> None:
    assert growth._score_cagr(cagr_value) == expected_score


@pytest.mark.unit
def test_score_cagr_none_passthrough() -> None:
    assert growth._score_cagr(None) is None


@pytest.mark.unit
def test_growth_full_picture_uses_mean() -> None:
    """4 annual entries of Revenue + EPS, FCF computable → score = mean(rev, eps, fcf)."""
    # Revenue: 100 → 133.1 over 3 years → exact 10 % CAGR → tier 80
    # EPS: 1.0 → 1.728 over 3 years → 20 % CAGR → tier 100
    # FCF (OCF − CapEx): y-3 net=50, y0 net = 79.95 → CAGR ≈ 16.9 % → tier 100
    facts = make_facts({
        "Revenues": [
            ("2021-12-31", 100),
            ("2022-12-31", 110),
            ("2023-12-31", 121),
            ("2024-12-31", 133.1),
        ],
        "EarningsPerShareDiluted": [
            ("2021-12-31", 1.0),
            ("2022-12-31", 1.2),
            ("2023-12-31", 1.44),
            ("2024-12-31", 1.728),
        ],
        "NetCashProvidedByUsedInOperatingActivities": [
            ("2021-12-31", 60),
            ("2022-12-31", 70),
            ("2023-12-31", 84),
            ("2024-12-31", 90),
        ],
        "PaymentsToAcquirePropertyPlantAndEquipment": [
            ("2021-12-31", 10),
            ("2022-12-31", 10),
            ("2023-12-31", 10),
            ("2024-12-31", 10.05),
        ],
    })
    result = growth.compute(facts)
    assert result.available is True
    # Sub-scores: rev=80, eps=100, fcf=100 → mean = 93.3
    assert result.sub_scores["revenue_cagr_3y"] == 80
    assert result.sub_scores["eps_cagr_3y"] == 100
    assert result.sub_scores["fcf_cagr_3y"] == 100
    assert result.score == round((80 + 100 + 100) / 3, 1)


@pytest.mark.unit
def test_growth_insufficient_history_returns_none() -> None:
    """Only 3 entries → no 3-year CAGR computable."""
    facts = make_facts({
        "Revenues": [
            ("2022-12-31", 100),
            ("2023-12-31", 110),
            ("2024-12-31", 121),
        ],
    })
    result = growth.compute(facts)
    assert result.available is False
    assert result.score is None
