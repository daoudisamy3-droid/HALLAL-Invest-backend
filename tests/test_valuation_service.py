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


# ─── Methods 1, 2, 4 — unavailable when YFinance inputs absent ──────────────


@pytest.mark.unit
def test_methods_1_2_4_unavailable_without_yfinance_inputs() -> None:
    """Without YFinance history / .info, M1 + M4 fail gracefully; M2 still V1 stub."""
    facts = make_facts({
        "EarningsPerShareDiluted":      [("2024-12-31", 5)],
        "StockholdersEquity":           [("2024-12-31", 100)],
        "CommonStockSharesOutstanding": [("2024-12-31", 10)],
    })
    m1 = vs._method_vs_historical_5y(facts, history=None, current_price=None)
    m2 = vs._method_vs_sector(facts)
    m4 = vs._method_analyst_target(info=None)

    for m, name in [(m1, "vs_historical_5y"), (m2, "vs_sector"), (m4, "analyst_target")]:
        assert m.available is False, f"{name} should be unavailable"
        assert m.fair_value is None
        assert m.reason


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


# ─── Step 5 — Method 1 (multiples vs 5y) unit tests ─────────────────────────


@pytest.mark.unit
def test_method1_returns_unavailable_when_history_none() -> None:
    facts = make_facts({"EarningsPerShareDiluted": [("2024-12-31", 5)]})
    m = vs._method_vs_historical_5y(facts, history=None, current_price=Decimal("100"))
    assert m.available is False
    assert m.reason and "YFinance" in m.reason


@pytest.mark.unit
def test_method1_returns_unavailable_when_eps_history_too_short() -> None:
    """Only the latest year of EPS → can't compute historical median P/E."""
    facts = make_facts({"EarningsPerShareDiluted": [("2024-12-31", 5)]})
    bars = [{"date": f"2024-{m:02d}-28", "close": 50.0} for m in range(1, 13)]
    m = vs._method_vs_historical_5y(facts, history=bars, current_price=Decimal("50"))
    assert m.available is False
    assert m.reason and "EPS" in m.reason


@pytest.mark.unit
def test_method1_returns_unavailable_when_latest_eps_non_positive() -> None:
    facts = make_facts({
        "EarningsPerShareDiluted": [
            ("2024-12-31", Decimal("-1")),
            ("2023-12-31", Decimal("5")),
        ],
    })
    bars = [{"date": "2024-06-30", "close": 50.0}]
    m = vs._method_vs_historical_5y(facts, history=bars, current_price=Decimal("50"))
    assert m.available is False
    assert m.reason and "non-positif" in m.reason


@pytest.mark.unit
def test_method1_computes_fair_value_from_pe_median_exact() -> None:
    """Deterministic case: EPS constant at 2.0 across years, 24 monthly bars
    all priced at 30 → historical P/E = 15 → fair_value = 15 × 2 = 30 (exact)."""
    facts = make_facts({
        "EarningsPerShareDiluted": [
            ("2024-12-31", Decimal("2")),
            ("2023-12-31", Decimal("2")),
            ("2022-12-31", Decimal("2")),
        ],
    })
    bars = []
    for year in (2023, 2024):
        for month in range(1, 13):
            day = 28
            bars.append({"date": f"{year}-{month:02d}-{day:02d}", "close": 30.0})

    m = vs._method_vs_historical_5y(
        facts, history=bars, current_price=Decimal("30")
    )
    assert m.available is True
    assert m.fair_value == Decimal("30")
    assert Decimal(m.details["latest_eps"]) == Decimal("2")
    assert m.details["n_pe_observations"] == 24


@pytest.mark.unit
def test_method1_rejects_when_fewer_than_12_observations() -> None:
    facts = make_facts({
        "EarningsPerShareDiluted": [
            ("2024-12-31", Decimal("2")),
            ("2023-12-31", Decimal("2")),
        ],
    })
    # Only 6 valid bars
    bars = [{"date": f"2024-{m:02d}-28", "close": 30.0} for m in range(1, 7)]
    m = vs._method_vs_historical_5y(facts, history=bars, current_price=Decimal("30"))
    assert m.available is False
    assert m.reason and "12" in m.reason


# ─── Step 5 — Method 4 (analyst target) unit tests ──────────────────────────


@pytest.mark.unit
def test_method4_unavailable_when_info_none() -> None:
    m = vs._method_analyst_target(None)
    assert m.available is False
    assert m.reason and "YFinance" in m.reason


@pytest.mark.unit
def test_method4_unavailable_when_target_missing() -> None:
    m = vs._method_analyst_target({"numberOfAnalystOpinions": 20})
    assert m.available is False
    assert m.reason


@pytest.mark.unit
def test_method4_unavailable_when_fewer_than_5_analysts() -> None:
    m = vs._method_analyst_target({
        "targetMedianPrice": 250.0,
        "numberOfAnalystOpinions": 3,
    })
    assert m.available is False
    assert m.reason and ("5" in m.reason or "analyst" in m.reason)


@pytest.mark.unit
def test_method4_available_with_valid_target_and_enough_analysts() -> None:
    m = vs._method_analyst_target({
        "targetMedianPrice": 250.0,
        "targetLowPrice": 200.0,
        "targetHighPrice": 320.0,
        "numberOfAnalystOpinions": 25,
    })
    assert m.available is True
    assert m.fair_value == Decimal("250.0")
    assert m.details["number_of_analyst_opinions"] == 25


# ─── Step 5 — end-to-end with YFinance wired ────────────────────────────────


@pytest.mark.integration
async def test_e2e_with_yfinance_methods_1_3_4_available(db, monkeypatch) -> None:
    """When YFinance returns useful data, M1 + M3 + M4 all become available
    and the verdict computes against a real ratio (current_price / median)."""
    facts = make_facts({
        "EarningsPerShareDiluted": [
            ("2024-12-31", Decimal("2")),
            ("2023-12-31", Decimal("2")),
        ],
        "StockholdersEquity":           [("2024-12-31", 10)],
        "CommonStockSharesOutstanding": [("2024-12-31", 1)],  # → Graham 15
    })
    monkeypatch.setattr(
        vs.financials_service,
        "get_facts_payload",
        AsyncMock(return_value=(320193, "Test Corp", facts)),
    )

    class _FakeYF:
        async def get_info(self, _s):
            return {
                "regularMarketPrice": 30.0,
                "targetMedianPrice": 40.0,
                "numberOfAnalystOpinions": 20,
            }

        async def get_history(self, _s, **_kw):
            return [
                {"date": f"2024-{m:02d}-28", "close": 30.0}
                for m in range(1, 13)
            ] + [
                {"date": f"2023-{m:02d}-28", "close": 30.0}
                for m in range(1, 13)
            ]

    report = await vs.compute_valuation(
        "TST", db, AsyncMock(), yfinance_client=_FakeYF(),
    )

    # Graham=15, M1=30, M4=40 → 3 methods, median=30
    assert report.n_methods == 3
    assert report.methods["graham_number"].available is True
    assert report.methods["vs_historical_5y"].available is True
    assert report.methods["analyst_target"].available is True
    assert report.methods["vs_sector"].available is False  # still V1 stub
    assert report.fair_value_median == Decimal("30")
    assert report.current_price == Decimal("30")
    # ratio = 30/30 = 1.0 → JUSTE PRIX
    assert report.ratio_price_to_fair_value == Decimal("1")
    assert report.verdict == "OUI_NEUTRE"
    assert report.label == "JUSTE PRIX"
