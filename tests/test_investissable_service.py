"""End-to-end orchestration tests for app/services/investissable_service.py.

We mock the Halal Terminal + SEC EDGAR clients to inject crafted
upstream data, and verify each gate cascade + the final verdict logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core.exceptions import SECEdgarError
from app.schemas.shariah import ShariahReport
from app.services import investissable_service
from app.services.investissable_service import compute_investissable
from tests._score_helpers import make_facts, make_submissions


# ─── Helpers ────────────────────────────────────────────────────────────────


def _shariah_pass() -> ShariahReport:
    return ShariahReport(
        symbol="TST",
        verdict="PASS",
        source="Halal Terminal API (aggregate verdict)",
    )


def _shariah_fail() -> ShariahReport:
    return ShariahReport(
        symbol="TST",
        verdict="FAIL",
        source="Halal Terminal API (aggregate verdict)",
        reason="Failed financial screen.",
    )


def _shariah_not_covered() -> ShariahReport:
    return ShariahReport(
        symbol="TST",
        verdict="NOT_COVERED",
        source="Halal Terminal API (aggregate verdict)",
        reason="TST non couvert par Halal Terminal",
    )


def _mock_halal_client(report: ShariahReport) -> AsyncMock:
    client = AsyncMock()
    # patched on the service's screen function, so we just need a dummy here.
    return client


def _facts_high_quality() -> dict:
    """4 years of data designed to make every available component score well."""
    return make_facts({
        # Balance sheet (Altman)
        "AssetsCurrent":      [("2024-12-31", 300)],
        "LiabilitiesCurrent": [("2024-12-31", 150)],
        "RetainedEarningsAccumulatedDeficit": [("2024-12-31", 500)],
        "OperatingIncomeLoss": [("2024-12-31", 200)],
        "StockholdersEquity":  [("2024-12-31", 800)],
        "Liabilities":         [("2024-12-31", 400)],
        "Assets":              [("2024-12-31", 1200)],
        # Year-over-year for Piotroski
        "NetIncomeLoss":       [
            ("2023-12-31", 100), ("2024-12-31", 150)],
        "LongTermDebt":         [
            ("2023-12-31", 250), ("2024-12-31", 200)],
        "GrossProfit":          [
            ("2023-12-31", 400), ("2024-12-31", 500)],
        "CommonStockSharesOutstanding": [
            ("2021-12-31", 1_000_000),
            ("2022-12-31", 1_000_000),
            ("2023-12-31", 1_000_000),
            ("2024-12-31",   950_000),     # -5 % over 3y → tier 100 if FCF covers
        ],
        # 4 years of Revenue / OCF / CapEx / EPS for Growth + Capital
        "Revenues": [
            ("2021-12-31", 1000), ("2022-12-31", 1100),
            ("2023-12-31", 1210), ("2024-12-31", 1331),
        ],
        "EarningsPerShareDiluted": [
            ("2021-12-31", 1.0), ("2022-12-31", 1.1),
            ("2023-12-31", 1.21), ("2024-12-31", 1.331),
        ],
        "NetCashProvidedByUsedInOperatingActivities": [
            ("2021-12-31", 200), ("2022-12-31", 220),
            ("2023-12-31", 242), ("2024-12-31", 266),
        ],
        "PaymentsToAcquirePropertyPlantAndEquipment": [
            ("2021-12-31", 60), ("2022-12-31", 60),
            ("2023-12-31", 60), ("2024-12-31", 100),
        ],
        "PaymentsForRepurchaseOfCommonStock": [
            ("2022-12-31", 50), ("2023-12-31", 50), ("2024-12-31", 50),
        ],
        "ResearchAndDevelopmentExpense": [("2024-12-31", 50)],
        "AccountsReceivableNetCurrent": [
            ("2022-12-31", 100), ("2023-12-31", 110), ("2024-12-31", 120),
        ],
    })


# ─── Tests ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_aaoifi_fail_shortcircuits_verdict(db, monkeypatch) -> None:
    monkeypatch.setattr(
        investissable_service.shariah_service,
        "screen_with_personal_thresholds",
        AsyncMock(return_value=_shariah_fail()),
    )
    sec_client = AsyncMock()

    report = await compute_investissable("AAPL", db, AsyncMock(), sec_client)
    assert report.verdict == "NON"
    assert report.blocked_at == "aaoifi"
    assert "altman" not in report.gates  # short-circuit ⇒ subsequent gates not evaluated
    sec_client.lookup_cik.assert_not_called()


@pytest.mark.integration
async def test_aaoifi_error_returns_incertain(db, monkeypatch) -> None:
    error_report = ShariahReport(
        symbol="TST",
        verdict="ERROR",
        source="Halal Terminal API (error)",
        reason="upstream 500",
    )
    monkeypatch.setattr(
        investissable_service.shariah_service,
        "screen_with_personal_thresholds",
        AsyncMock(return_value=error_report),
    )

    report = await compute_investissable("AAPL", db, AsyncMock(), AsyncMock())
    assert report.verdict == "INCERTAIN"
    assert report.blocked_at == "aaoifi"


@pytest.mark.integration
async def test_non_us_ticker_returns_incertain_with_warning(db, monkeypatch) -> None:
    monkeypatch.setattr(
        investissable_service.shariah_service,
        "screen_with_personal_thresholds",
        AsyncMock(return_value=_shariah_not_covered()),
    )
    sec_client = AsyncMock()
    sec_client.lookup_cik = AsyncMock(return_value=None)  # AIXA.DE not in SEC universe

    report = await compute_investissable("AIXA.DE", db, AsyncMock(), sec_client)
    # AAOIFI here is NOT_COVERED → blocks before sec lookup; treat as INCERTAIN.
    assert report.verdict == "INCERTAIN"
    assert report.blocked_at == "aaoifi"


@pytest.mark.integration
async def test_full_pipeline_passes_with_oui_verdict(db, monkeypatch) -> None:
    """All gates pass + quality data → verdict OUI with a label."""
    monkeypatch.setattr(
        investissable_service.shariah_service,
        "screen_with_personal_thresholds",
        AsyncMock(return_value=_shariah_pass()),
    )
    sec_client = AsyncMock()
    sec_client.lookup_cik = AsyncMock(return_value=(999, "Test Corp"))
    sec_client.get_company_facts = AsyncMock(return_value=_facts_high_quality())
    sec_client.get_submissions = AsyncMock(
        return_value=make_submissions(sic_code=7372, forms=[]),
    )

    report = await compute_investissable("TST", db, AsyncMock(), sec_client)
    assert report.verdict == "OUI"
    assert report.label is not None and report.label.startswith("OUI - QUALITÉ")
    assert report.gates["aaoifi"].verdict == "PASS"
    assert report.gates["altman"].verdict in {"PASS", "WARNING"}
    assert report.gates["fraud"].verdict == "PASS"
    # Piotroski / Growth / Capital available; SmartMoney + EarningsStab N/A
    assert report.quality_components["piotroski"].available is True
    assert report.quality_components["smart_money"].available is False
    assert report.quality_components["earnings_stability"].available is False
    assert report.data_completeness == "PARTIAL"
    # Warnings present for the 2 unavailable components
    warning_text = " ".join(report.warnings)
    assert "Smart Money" in warning_text
    assert "Earnings Stability" in warning_text


@pytest.mark.integration
async def test_sec_edgar_transient_failure_returns_incertain(db, monkeypatch) -> None:
    monkeypatch.setattr(
        investissable_service.shariah_service,
        "screen_with_personal_thresholds",
        AsyncMock(return_value=_shariah_pass()),
    )
    sec_client = AsyncMock()
    sec_client.lookup_cik = AsyncMock(side_effect=SECEdgarError("upstream 500"))

    report = await compute_investissable("AAPL", db, AsyncMock(), sec_client)
    assert report.verdict == "INCERTAIN"
    assert "SEC EDGAR" in (report.reason or "")
