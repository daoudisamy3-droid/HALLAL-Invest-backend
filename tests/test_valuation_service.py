"""Tests for app/services/valuation_service.py — Graham + aggregation + verdict.

Decimal-exact asserts on the deterministic cases (perfect-square Graham,
verdict thresholds). Irrational sqrt cases use a small tolerance.
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import SECEdgarError
from app.schemas.valuation import MethodResult
from app.services import valuation_service as vs
from tests._score_helpers import make_facts


# ─── Graham — deterministic perfect-square ──────────────────────────────────


@pytest.mark.unit
def test_graham_perfect_square_returns_15_exact() -> None:
    """EPS=1, BVPS=10 → inside = 22.5 × 1 × 10 = 225 → sqrt = 15.0 (exact)."""
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", 1)],
        "StockholdersEquity":           [("2024-12-31", 10)],
        "CommonStockSharesOutstanding": [("2024-12-31", 1)],   # BVPS = 10/1 = 10
    })
    result = vs._method_graham_number(facts)
    assert result.available is True
    assert result.fair_value == Decimal("15")


@pytest.mark.unit
def test_graham_real_world_bvps_division() -> None:
    """EPS=4, equity=40, shares=10 → BVPS=4 → inside=22.5×4×4=360 → sqrt≈18.97."""
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", 4)],
        "StockholdersEquity":           [("2024-12-31", 40)],
        "CommonStockSharesOutstanding": [("2024-12-31", 10)],
    })
    result = vs._method_graham_number(facts)
    assert result.available is True
    # sqrt(360) is irrational; Decimal.sqrt() works under context precision
    # (default 28 digits). Compare to a known approximation with a tolerance
    # that's tighter than any plausible monetary precision (1e-10).
    expected = Decimal("18.9736659610102")
    assert result.fair_value is not None
    assert abs(result.fair_value - expected) < Decimal("1e-10")


@pytest.mark.unit
def test_graham_unavailable_when_eps_zero() -> None:
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", 0)],
        "StockholdersEquity":           [("2024-12-31", 100)],
        "CommonStockSharesOutstanding": [("2024-12-31", 10)],
    })
    result = vs._method_graham_number(facts)
    assert result.available is False
    assert "EPS" in (result.reason or "") or "négatif" in (result.reason or "")


@pytest.mark.unit
def test_graham_unavailable_when_eps_negative() -> None:
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", -2)],
        "StockholdersEquity":           [("2024-12-31", 100)],
        "CommonStockSharesOutstanding": [("2024-12-31", 10)],
    })
    result = vs._method_graham_number(facts)
    assert result.available is False


@pytest.mark.unit
def test_graham_unavailable_when_bvps_negative() -> None:
    """Negative book value (accumulated deficit > paid-in capital)."""
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", 5)],
        "StockholdersEquity":           [("2024-12-31", -100)],
        "CommonStockSharesOutstanding": [("2024-12-31", 10)],
    })
    result = vs._method_graham_number(facts)
    assert result.available is False
    assert "BVPS" in (result.reason or "") or "négatif" in (result.reason or "")


@pytest.mark.unit
def test_graham_unavailable_when_shares_missing() -> None:
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", 5)],
        "StockholdersEquity":           [("2024-12-31", 100)],
        # CommonStockSharesOutstanding missing
    })
    result = vs._method_graham_number(facts)
    assert result.available is False
    assert "shares_outstanding" in (result.reason or "")


# ─── Methods 1, 2, 4 — always unavailable in V1 ──────────────────────────────


@pytest.mark.unit
def test_methods_1_2_4_always_unavailable_in_v1() -> None:
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", 5)],
        "StockholdersEquity":           [("2024-12-31", 100)],
        "CommonStockSharesOutstanding": [("2024-12-31", 10)],
    })
    m1 = vs._method_vs_historical_5y(facts)
    m2 = vs._method_vs_sector(facts)
    m4 = vs._method_analyst_target(facts)

    for m, name in [(m1, "vs_historical_5y"), (m2, "vs_sector"), (m4, "analyst_target")]:
        assert m.available is False, f"{name} should be unavailable in V1"
        assert m.fair_value is None
        assert m.reason is not None
        assert "V1" in m.reason


# ─── Aggregation ────────────────────────────────────────────────────────────


def _avail(fv: Decimal | int | float) -> MethodResult:
    return MethodResult(
        available=True, fair_value=Decimal(str(fv)), details={}, reason=None,
    )


def _unavail() -> MethodResult:
    return MethodResult(available=False, fair_value=None, details={}, reason="V1")


@pytest.mark.unit
def test_aggregate_single_method_returns_indetermine() -> None:
    """V1 case: only Graham available → INDÉTERMINÉ."""
    methods = {
        "vs_historical_5y": _unavail(),
        "vs_sector":        _unavail(),
        "graham_number":    _avail(33),
        "analyst_target":   _unavail(),
    }
    agg = vs._aggregate(methods, current_price=None)
    assert agg["n_methods"] == 1
    assert agg["fair_value_median"] == Decimal("33")
    assert agg["fair_value_mean"] == Decimal("33")
    assert agg["dispersion"] is None
    assert agg["ratio"] is None
    assert agg["verdict"] == "INDÉTERMINÉ"
    assert agg["confidence"] == "N/A"
    assert "2 méthodes" in (agg["reason"] or "")


@pytest.mark.unit
def test_aggregate_two_methods_computes_median_mean_dispersion() -> None:
    """Methods with values 100 and 150 → median=125, mean=125, disp=(150-100)/125=0.4."""
    methods = {
        "vs_historical_5y": _avail(100),
        "vs_sector":        _avail(150),
        "graham_number":    _unavail(),
        "analyst_target":   _unavail(),
    }
    agg = vs._aggregate(methods, current_price=Decimal("100"))
    assert agg["n_methods"] == 2
    assert agg["fair_value_median"] == Decimal("125")
    assert agg["fair_value_mean"] == Decimal("125")
    assert agg["dispersion"] == Decimal("0.4")
    # ratio = 100 / 125 = 0.8 → SOUS-ÉVALUÉE
    assert agg["ratio"] == Decimal("0.8")
    assert agg["verdict"] == "OUI"
    assert agg["label"] == "SOUS-ÉVALUÉE"
    assert agg["confidence"] == "LOW"  # 2 methods → LOW


@pytest.mark.unit
def test_aggregate_four_methods_high_confidence() -> None:
    """4 methods with low dispersion → HIGH confidence."""
    methods = {
        "vs_historical_5y": _avail(100),
        "vs_sector":        _avail(105),
        "graham_number":    _avail(102),
        "analyst_target":   _avail(108),
    }
    # Sorted: [100, 102, 105, 108] → median = (102+105)/2 = 103.5
    # mean = 415/4 = 103.75
    # dispersion = (108-100)/103.5 = 8/103.5 ≈ 0.0773 (< 0.15)
    agg = vs._aggregate(methods, current_price=Decimal("104"))
    assert agg["n_methods"] == 4
    assert agg["fair_value_median"] == Decimal("103.5")
    assert agg["fair_value_mean"] == Decimal("103.75")
    # dispersion ≈ 0.0773
    assert agg["dispersion"] is not None
    assert abs(agg["dispersion"] - Decimal("0.0773")) < Decimal("0.001")
    assert agg["confidence"] == "HIGH"
    # ratio = 104/103.5 ≈ 1.0048 → JUSTE PRIX (in [0.95, 1.05))
    assert agg["verdict"] == "OUI_NEUTRE"
    assert agg["label"] == "JUSTE PRIX"


# ─── Verdict thresholds (§4.3.6) ────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "ratio_str, expected_verdict, expected_label",
    [
        ("0.80", "OUI",        "SOUS-ÉVALUÉE"),
        ("0.84", "OUI",        "SOUS-ÉVALUÉE"),
        ("0.85", "OUI",        "JUSTE PRIX (LÉGÈRE DÉCOTE)"),  # boundary inclusive
        ("0.90", "OUI",        "JUSTE PRIX (LÉGÈRE DÉCOTE)"),
        ("0.95", "OUI_NEUTRE", "JUSTE PRIX"),                  # boundary inclusive
        ("1.00", "OUI_NEUTRE", "JUSTE PRIX"),
        ("1.04", "OUI_NEUTRE", "JUSTE PRIX"),
        ("1.05", "NON",        "SURÉVALUÉE"),                  # boundary inclusive
        ("1.20", "NON",        "SURÉVALUÉE"),
        ("1.50", "NON",        "FORTEMENT SURÉVALUÉE"),        # boundary inclusive
        ("2.00", "NON",        "FORTEMENT SURÉVALUÉE"),
    ],
)
def test_verdict_label_thresholds(
    ratio_str: str, expected_verdict: str, expected_label: str,
) -> None:
    verdict, label = vs._verdict_label(Decimal(ratio_str))
    assert verdict == expected_verdict
    assert label == expected_label


# ─── Confidence ladder ──────────────────────────────────────────────────────


@pytest.mark.unit
@pytest.mark.parametrize(
    "n, dispersion_str, expected",
    [
        (0, None,     "N/A"),
        (1, None,     "N/A"),
        (2, "0.05",   "LOW"),    # 2 methods always LOW
        (3, "0.05",   "MEDIUM"), # 3 methods always MEDIUM
        (4, "0.10",   "HIGH"),   # 4 methods + dispersion < 0.15
        (4, "0.20",   "MEDIUM"), # 4 methods + 0.15 ≤ disp < 0.30
        (4, "0.35",   "LOW"),    # 4 methods + dispersion ≥ 0.30
    ],
)
def test_confidence_ladder(n: int, dispersion_str: str | None, expected: str) -> None:
    dispersion = Decimal(dispersion_str) if dispersion_str else None
    assert vs._confidence(n, dispersion) == expected


# ─── End-to-end orchestration ───────────────────────────────────────────────


@pytest.mark.integration
async def test_e2e_returns_indetermine_with_graham_exposed(db, monkeypatch) -> None:
    """The V1 happy path: only Graham works → INDÉTERMINÉ verdict but
    Graham number visible in methods.graham_number.
    """
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", 1)],
        "StockholdersEquity":           [("2024-12-31", 10)],
        "CommonStockSharesOutstanding": [("2024-12-31", 1)],
    })
    sec_client = AsyncMock()
    monkeypatch.setattr(
        vs.financials_service,
        "get_facts_payload",
        AsyncMock(return_value=(320193, "Test Corp", facts)),
    )

    report = await vs.compute_valuation("TST", db, sec_client)

    assert report.verdict == "INDÉTERMINÉ"
    assert report.confidence == "N/A"
    assert report.n_methods == 1
    assert report.methods["graham_number"].available is True
    assert report.methods["graham_number"].fair_value == Decimal("15")
    assert report.fair_value_median == Decimal("15")
    # The other 3 methods are unavailable
    for name in ("vs_historical_5y", "vs_sector", "analyst_target"):
        assert report.methods[name].available is False
    # Warning surfacing the limitation
    assert any("Graham" in w or "VALUATION_PARTIAL_METHODS" in w for w in report.warnings)


@pytest.mark.integration
async def test_e2e_non_us_ticker_indetermine_with_warning(db, monkeypatch) -> None:
    """Non-US ticker (not in SEC ticker map) → INDÉTERMINÉ + non-US warning."""
    monkeypatch.setattr(
        vs.financials_service,
        "get_facts_payload",
        AsyncMock(return_value=None),  # SEC ticker miss
    )
    report = await vs.compute_valuation("AIXA.DE", db, AsyncMock())
    assert report.verdict == "INDÉTERMINÉ"
    assert report.n_methods == 0
    assert "non-US" in (report.reason or "")
    assert any("non-US" in w or "INVESTISSABLE_NON_US_LIMITATION" in w for w in report.warnings)


@pytest.mark.integration
async def test_e2e_sec_edgar_failure_returns_indetermine(db, monkeypatch) -> None:
    """SEC EDGAR transient error → INDÉTERMINÉ + warning."""
    monkeypatch.setattr(
        vs.financials_service,
        "get_facts_payload",
        AsyncMock(side_effect=SECEdgarError("upstream 500")),
    )
    report = await vs.compute_valuation("AAPL", db, AsyncMock())
    assert report.verdict == "INDÉTERMINÉ"
    assert "SEC EDGAR" in (report.reason or "")
    assert report.n_methods == 0
