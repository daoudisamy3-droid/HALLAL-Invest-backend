"""Tests for app/services/capital_allocation.py — quality component D."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services import capital_allocation
from app.services.sector import SectorInfo
from tests._score_helpers import make_facts


_NEUTRAL_SECTOR = SectorInfo(
    sic_code=7372,
    sic_description="Software",
    is_exempt_from_absolute_fcf_ni=False,
    exempt_reason=None,
)


def _facts_with_buybacks_and_fcf(
    *,
    shares_y_minus_3: int,
    shares_y0: int,
    ocf_3y: list[int],            # [y-2, y-1, y0]
    capex_3y: list[int],
    buybacks_3y: list[int],
    capex_latest: int = 100,
    rd_latest: int = 100,
    revenue_latest: int = 1000,
) -> dict:
    """Helper for D1+D3 fixture data."""
    return make_facts({
        "CommonStockSharesOutstanding": [
            ("2021-12-31", shares_y_minus_3),
            ("2022-12-31", shares_y_minus_3),
            ("2023-12-31", shares_y_minus_3),
            ("2024-12-31", shares_y0),
        ],
        "NetCashProvidedByUsedInOperatingActivities": [
            ("2022-12-31", ocf_3y[0]),
            ("2023-12-31", ocf_3y[1]),
            ("2024-12-31", ocf_3y[2]),
        ],
        "PaymentsToAcquirePropertyPlantAndEquipment": [
            ("2022-12-31", capex_3y[0]),
            ("2023-12-31", capex_3y[1]),
            ("2024-12-31", capex_latest),     # latest is what D3 reads
        ],
        "PaymentsForRepurchaseOfCommonStock": [
            ("2022-12-31", buybacks_3y[0]),
            ("2023-12-31", buybacks_3y[1]),
            ("2024-12-31", buybacks_3y[2]),
        ],
        "ResearchAndDevelopmentExpense": [("2024-12-31", rd_latest)],
        "Revenues": [("2024-12-31", revenue_latest)],
    })


@pytest.mark.unit
def test_d1_excellent_when_reduction_and_fcf_covers() -> None:
    """Shares -10 % (5 % reduction tier) AND total FCF ≥ total buybacks → D1 = 100."""
    facts = _facts_with_buybacks_and_fcf(
        shares_y_minus_3=1_000_000,
        shares_y0=900_000,           # -10 % reduction
        ocf_3y=[100, 110, 120],      # ΣOCF = 330
        capex_3y=[20, 20, 20],       # ΣCapEx = 60 → ΣFCF = 270
        buybacks_3y=[50, 60, 70],    # Σbuyback = 180  ≤ 270 ✓
    )
    result = capital_allocation.compute(facts, _NEUTRAL_SECTOR)
    assert result.d1_buybacks == 100


@pytest.mark.unit
def test_d1_dilution_score_20() -> None:
    """Shares increased → D1 = 20 (dilution)."""
    facts = _facts_with_buybacks_and_fcf(
        shares_y_minus_3=1_000_000,
        shares_y0=1_100_000,         # +10 % dilution
        ocf_3y=[100, 110, 120],
        capex_3y=[20, 20, 20],
        buybacks_3y=[0, 0, 0],
    )
    result = capital_allocation.compute(facts, _NEUTRAL_SECTOR)
    assert result.d1_buybacks == 20


@pytest.mark.unit
def test_d1_neutral_when_small_reduction_score_60() -> None:
    """Shares reduce by less than 5 % → D1 = 60 (neutral)."""
    facts = _facts_with_buybacks_and_fcf(
        shares_y_minus_3=1_000_000,
        shares_y0=980_000,            # -2 % reduction (< 5 %)
        ocf_3y=[100, 100, 100],
        capex_3y=[10, 10, 10],
        buybacks_3y=[0, 0, 0],
    )
    result = capital_allocation.compute(facts, _NEUTRAL_SECTOR)
    assert result.d1_buybacks == 60


@pytest.mark.unit
def test_d1_buyback_debt_financed_score_60() -> None:
    """Significant reduction BUT buybacks > FCF → D1 = 60 (debt-financed warning)."""
    facts = _facts_with_buybacks_and_fcf(
        shares_y_minus_3=1_000_000,
        shares_y0=900_000,            # -10 % reduction (tier "sensible")
        ocf_3y=[50, 50, 50],          # ΣOCF = 150
        capex_3y=[10, 10, 10],        # ΣFCF = 120
        buybacks_3y=[60, 60, 60],     # Σbuyback = 180 > 120
    )
    result = capital_allocation.compute(facts, _NEUTRAL_SECTOR)
    assert result.d1_buybacks == 60


@pytest.mark.unit
@pytest.mark.parametrize(
    "capex, rd, revenue, expected_d3",
    [
        ( 50,  50, 1000,  80),  # 10 % intensity → zone normale [0.05, 0.30]
        (100, 100, 1000,  80),  # 20 % intensity → zone normale (within range)
        (400,   0, 1000,  60),  # 40 % intensity → too intensive (> 0.30)
        ( 10,  10, 1000,  50),  # 2 % intensity → too low (< 0.05)
    ],
)
def test_d3_investment_intensity_absolute_thresholds(
    capex: int, rd: int, revenue: int, expected_d3: int
) -> None:
    facts = _facts_with_buybacks_and_fcf(
        shares_y_minus_3=1_000_000,
        shares_y0=1_000_000,
        ocf_3y=[100, 100, 100],
        capex_3y=[capex, capex, capex],
        buybacks_3y=[0, 0, 0],
        capex_latest=capex,
        rd_latest=rd,
        revenue_latest=revenue,
    )
    result = capital_allocation.compute(facts, _NEUTRAL_SECTOR)
    assert result.d3_investment_intensity == expected_d3
