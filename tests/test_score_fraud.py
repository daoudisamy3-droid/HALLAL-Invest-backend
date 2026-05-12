"""Tests for app/services/fraud.py — Gate 3 (3 signals)."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import fraud
from app.services.sector import SectorInfo
from tests._score_helpers import make_facts, make_submissions


_NEUTRAL_SECTOR = SectorInfo(
    sic_code=7372, sic_description="Software",
    is_exempt_from_absolute_fcf_ni=False, exempt_reason=None,
)

_EXEMPT_SECTOR = SectorInfo(
    sic_code=1311, sic_description="Crude Petroleum",
    is_exempt_from_absolute_fcf_ni=True,
    exempt_reason="SIC 1311 in capital-intensive exempt range [1000, 1499]",
)


def _facts_for_fraud(
    *,
    ocf: list[int],          # [y-2, y-1, y0]
    capex: list[int],
    ni: list[int],
    receivables: list[int],  # 3 years
    revenues: list[int],     # 3 years
) -> dict:
    end_dates = ("2022-12-31", "2023-12-31", "2024-12-31")
    return make_facts({
        "NetCashProvidedByUsedInOperatingActivities": list(zip(end_dates, ocf)),
        "PaymentsToAcquirePropertyPlantAndEquipment": list(zip(end_dates, capex)),
        "NetIncomeLoss": list(zip(end_dates, ni)),
        "AccountsReceivableNetCurrent": list(zip(end_dates, receivables)),
        "Revenues": list(zip(end_dates, revenues)),
    })


# ─── Signal 1 — FCF/NI ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_signal_1_clean_ratio_above_threshold() -> None:
    """FCF/NI = 200/100 = 2.0 ≥ 0.5 → not flagged."""
    facts = _facts_for_fraud(
        ocf=[100, 110, 120],         # ΣOCF=330
        capex=[40, 40, 50],          # ΣCapEx=130 → ΣFCF=200
        ni=[30, 35, 35],             # ΣNI=100
        receivables=[100, 100, 100],
        revenues=[1000, 1000, 1000],
    )
    res = fraud.compute(facts, _NEUTRAL_SECTOR, submissions=None)
    assert res.signals["fcf_ni"].positive is False


@pytest.mark.unit
def test_signal_1_alert_below_threshold() -> None:
    """FCF/NI = 20/100 = 0.2 < 0.5 → flagged."""
    facts = _facts_for_fraud(
        ocf=[50, 50, 50],            # ΣOCF=150
        capex=[40, 50, 40],          # ΣCapEx=130 → ΣFCF=20
        ni=[30, 35, 35],             # ΣNI=100
        receivables=[100, 100, 100],
        revenues=[1000, 1000, 1000],
    )
    res = fraud.compute(facts, _NEUTRAL_SECTOR, submissions=None)
    assert res.signals["fcf_ni"].positive is True


@pytest.mark.unit
def test_signal_1_exempt_sector_overrides_alert() -> None:
    """Same poor ratio but exempt sector → not flagged."""
    facts = _facts_for_fraud(
        ocf=[50, 50, 50],
        capex=[40, 50, 40],
        ni=[30, 35, 35],
        receivables=[100, 100, 100],
        revenues=[1000, 1000, 1000],
    )
    res = fraud.compute(facts, _EXEMPT_SECTOR, submissions=None)
    assert res.signals["fcf_ni"].positive is False
    assert "exempt" in res.signals["fcf_ni"].details["exempt_reason"].lower()


# ─── Signal 2 — Receivables anomaly ─────────────────────────────────────────


@pytest.mark.unit
def test_signal_2_clean_when_receivables_track_revenue() -> None:
    """Receivables CAGR ≈ Revenue CAGR → not flagged."""
    facts = _facts_for_fraud(
        ocf=[100, 100, 100], capex=[20, 20, 20], ni=[50, 50, 50],
        receivables=[100, 110, 121],
        revenues=[1000, 1100, 1210],
    )
    res = fraud.compute(facts, _NEUTRAL_SECTOR, submissions=None)
    # rcv CAGR 2y = (121/100)^0.5 -1 ≈ 0.10
    # rev CAGR 2y = (1210/1000)^0.5 -1 ≈ 0.10
    # ratio ≈ 1.0, threshold 2.0 → clean
    assert res.signals["receivables"].positive is False


@pytest.mark.unit
def test_signal_2_alert_receivables_grow_2x_faster() -> None:
    facts = _facts_for_fraud(
        ocf=[100, 100, 100], capex=[20, 20, 20], ni=[50, 50, 50],
        receivables=[100, 130, 169],       # ~30 % CAGR 2y
        revenues=[1000, 1050, 1102.5],     # ~5 % CAGR 2y → ratio = 6 (way above 2.0)
    )
    res = fraud.compute(facts, _NEUTRAL_SECTOR, submissions=None)
    assert res.signals["receivables"].positive is True


# ─── Signal 3 — Restatements ────────────────────────────────────────────────


@pytest.mark.unit
def test_signal_3_clean_when_no_restatements() -> None:
    submissions = make_submissions(
        forms=[
            ("10-K", "2024-01-15"),
            ("10-Q", "2024-04-15"),
            ("10-Q", "2024-07-15"),
        ],
    )
    facts = _facts_for_fraud(
        ocf=[100, 100, 100], capex=[20, 20, 20], ni=[50, 50, 50],
        receivables=[100, 110, 121], revenues=[1000, 1100, 1210],
    )
    res = fraud.compute(facts, _NEUTRAL_SECTOR, submissions=submissions)
    assert res.signals["restatements"].positive is False


@pytest.mark.unit
def test_signal_3_alert_at_three_restatements() -> None:
    today = date.today()
    recent_str = (today - timedelta(days=30)).isoformat()
    submissions = make_submissions(
        forms=[
            ("10-K/A", recent_str),
            ("10-Q/A", recent_str),
            ("NT 10-K", recent_str),
        ],
    )
    facts = _facts_for_fraud(
        ocf=[100, 100, 100], capex=[20, 20, 20], ni=[50, 50, 50],
        receivables=[100, 110, 121], revenues=[1000, 1100, 1210],
    )
    res = fraud.compute(facts, _NEUTRAL_SECTOR, submissions=submissions)
    assert res.signals["restatements"].positive is True
    assert res.signals["restatements"].details["count"] == 3


@pytest.mark.unit
def test_signal_3_none_when_submissions_unavailable() -> None:
    facts = _facts_for_fraud(
        ocf=[100, 100, 100], capex=[20, 20, 20], ni=[50, 50, 50],
        receivables=[100, 110, 121], revenues=[1000, 1100, 1210],
    )
    res = fraud.compute(facts, _NEUTRAL_SECTOR, submissions=None)
    assert res.signals["restatements"].positive is None


# ─── Gate composition ──────────────────────────────────────────────────────


@pytest.mark.unit
def test_gate_passes_with_zero_or_one_positive_signal() -> None:
    """All three signals clean → PASS."""
    facts = _facts_for_fraud(
        ocf=[100, 100, 100], capex=[20, 20, 20], ni=[50, 50, 50],
        receivables=[100, 110, 121], revenues=[1000, 1100, 1210],
    )
    res = fraud.compute(facts, _NEUTRAL_SECTOR, submissions=make_submissions(forms=[]))
    assert res.verdict == "PASS"
    assert res.positive_signals_count == 0


@pytest.mark.unit
def test_gate_fails_with_two_positive_signals() -> None:
    """S1 + S2 both flagged → FAIL (2/3 threshold)."""
    today = date.today()
    submissions = make_submissions(forms=[
        ("10-K", (today - timedelta(days=30)).isoformat()),
    ])
    facts = _facts_for_fraud(
        ocf=[20, 20, 20], capex=[10, 10, 10], ni=[50, 50, 50],     # FCF=30, NI=150 → 0.2 < 0.5
        receivables=[100, 130, 169],                                # ~30 % vs 5 % CAGR → ratio 6
        revenues=[1000, 1050, 1102.5],
    )
    res = fraud.compute(facts, _NEUTRAL_SECTOR, submissions=submissions)
    assert res.verdict == "FAIL"
    assert res.positive_signals_count == 2
    assert "fcf_ni" in (res.reason or "")
    assert "receivables" in (res.reason or "")
