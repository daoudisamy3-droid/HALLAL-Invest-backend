"""Unit + integration tests for app/services/shariah_service.py.

The HalalTerminalClient is replaced by an AsyncMock so we test the
service logic in isolation. The DB is the real (SAVEPOINT-rolled-back)
session from conftest — this is what validates the
`screen_history`-as-cache contract.

Reference tickers (Q plan): AAPL, MSFT, RIO, AIXA.DE, EOG.
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.exceptions import HalalTerminalError
from app.models.screen_history import ScreenHistory
from app.services.shariah_service import (
    ShariahCustomThresholds,
    screen_with_personal_thresholds,
)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _compliant_payload(symbol: str) -> dict[str, Any]:
    """Halal Terminal payload shape (§3.2.1) — comfortably compliant."""
    return {
        "symbol": symbol,
        "overall_status": "compliant",
        "ratios": {
            "debt_to_marketcap": 0.020,
            "debt_to_assets": 0.085,
            "cash_to_marketcap": 0.0164,
            "impure_revenue_ratio": 0.0023,
            "interest_income_ratio": 0.0018,
            "receivables_to_assets": 0.082,
            "non_compliant_assets_ratio": 0.046,
        },
        "methodologies": {
            "AAOIFI": {"status": "compliant", "failed_ratios": []},
            "DJIM":   {"status": "compliant", "failed_ratios": []},
            "FTSE":   {"status": "compliant", "failed_ratios": []},
            "MSCI":   {"status": "compliant", "failed_ratios": []},
            "S&P":    {"status": "compliant", "failed_ratios": []},
        },
        "compliance_explanation": "All ratios well under thresholds.",
        "as_of_date": "2026-04-30",
    }


def _make_mock_client(payload: Any = None, raises: Exception | None = None) -> AsyncMock:
    client = AsyncMock()
    if raises is not None:
        client.screen.side_effect = raises
    else:
        client.screen.return_value = payload
    return client


# ─── §5.1 thresholds — anti-regression ──────────────────────────────────────


@pytest.mark.unit
def test_thresholds_match_spec_5_1_strict() -> None:
    """Anti-regression: the 4 bloquant thresholds must be 0.30/0.30/0.03/0.03.

    The 0.45 receivables threshold is exposed for info but does not gate.
    """
    assert ShariahCustomThresholds.DEBT_TO_MARKETCAP_MAX == 0.30
    assert ShariahCustomThresholds.CASH_TO_MARKETCAP_MAX == 0.30
    assert ShariahCustomThresholds.IMPURE_REVENUE_MAX == 0.03
    assert ShariahCustomThresholds.INTEREST_INCOME_MAX == 0.03
    assert ShariahCustomThresholds.RECEIVABLES_TO_ASSETS_MAX == 0.45


# ─── 5 reference tickers — happy path ───────────────────────────────────────


@pytest.mark.integration
@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "RIO", "AIXA.DE", "EOG"])
async def test_screen_5_reference_tickers_pass(db, symbol: str) -> None:
    mock_client = _make_mock_client(_compliant_payload(symbol))
    report = await screen_with_personal_thresholds(symbol.lower(), db, mock_client)

    assert report.symbol == symbol.upper()  # normalised
    assert report.verdict == "PASS"
    assert report.failed_checks == []
    assert set(report.checks.keys()) == {
        "debt_to_marketcap",
        "cash_to_marketcap",
        "impure_revenue_ratio",
        "interest_income_ratio",
    }
    assert all(c.passed for c in report.checks.values())
    assert report.checks["debt_to_marketcap"].threshold == 0.30
    assert report.checks["impure_revenue_ratio"].threshold == 0.03
    assert report.as_of_date == date(2026, 4, 30)
    assert report.source == "Halal Terminal API + custom thresholds"
    assert report.cached is False
    mock_client.screen.assert_awaited_once_with(symbol.upper())


# ─── FAIL marginal — one ratio just over ────────────────────────────────────


@pytest.mark.integration
async def test_fail_marginal_single_check(db) -> None:
    payload = _compliant_payload("XOM")
    payload["ratios"]["debt_to_marketcap"] = 0.31  # just over 0.30

    mock_client = _make_mock_client(payload)
    report = await screen_with_personal_thresholds("XOM", db, mock_client)

    assert report.verdict == "FAIL"
    assert report.failed_checks == ["debt_to_marketcap"]
    assert report.checks["debt_to_marketcap"].passed is False
    assert report.checks["debt_to_marketcap"].value == pytest.approx(0.31)
    assert report.checks["cash_to_marketcap"].passed is True


# ─── FAIL massif — multiple checks over ─────────────────────────────────────


@pytest.mark.integration
async def test_fail_multiple_checks(db) -> None:
    payload = _compliant_payload("BAC")
    payload["ratios"]["debt_to_marketcap"] = 0.45        # over 0.30
    payload["ratios"]["impure_revenue_ratio"] = 0.12     # over 0.03
    payload["ratios"]["interest_income_ratio"] = 0.20    # over 0.03

    mock_client = _make_mock_client(payload)
    report = await screen_with_personal_thresholds("BAC", db, mock_client)

    assert report.verdict == "FAIL"
    assert set(report.failed_checks) == {
        "debt_to_marketcap",
        "impure_revenue_ratio",
        "interest_income_ratio",
    }
    assert report.checks["cash_to_marketcap"].passed is True  # the lone survivor


# ─── NOT_COVERED — provider returns None ────────────────────────────────────


@pytest.mark.integration
async def test_not_covered_persists_row(db) -> None:
    mock_client = _make_mock_client(None)
    report = await screen_with_personal_thresholds("XYZ.XX", db, mock_client)

    assert report.verdict == "NOT_COVERED"
    assert report.cached is False
    assert "non couvert" in (report.reason or "")

    # Persistence: a screen_history row should now exist
    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "XYZ.XX")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].verdict == "NOT_COVERED"


# ─── ERROR — provider raises ────────────────────────────────────────────────


@pytest.mark.integration
async def test_error_not_persisted(db) -> None:
    mock_client = _make_mock_client(raises=HalalTerminalError("upstream 500"))
    report = await screen_with_personal_thresholds("AAPL", db, mock_client)

    assert report.verdict == "ERROR"
    assert "indisponible" in (report.reason or "").lower()

    # Persistence: NO row should be written for ERROR (spec §3.4)
    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "AAPL")
    )).scalars().all()
    assert rows == []


# ─── Cache MISS persists a row ──────────────────────────────────────────────


@pytest.mark.integration
async def test_cache_miss_persists_screen_history_row(db) -> None:
    mock_client = _make_mock_client(_compliant_payload("AAPL"))
    await screen_with_personal_thresholds("AAPL", db, mock_client)

    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "AAPL")
    )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.verdict == "PASS"
    assert row.screen_date == date.today()
    # ratios_json must contain enough to reconstruct the report
    payload = row.ratios_json
    assert "raw_ratios" in payload
    assert "checks" in payload
    assert "halal_terminal_methodology_verdicts" in payload
    assert payload["as_of_date"] == "2026-04-30"
    assert payload["failed_checks"] == []


# ─── Cache HIT — second call within TTL avoids upstream ─────────────────────


@pytest.mark.integration
async def test_cache_hit_within_ttl_skips_upstream(db) -> None:
    mock_client = _make_mock_client(_compliant_payload("MSFT"))

    first = await screen_with_personal_thresholds("MSFT", db, mock_client)
    assert first.cached is False
    assert mock_client.screen.await_count == 1

    second = await screen_with_personal_thresholds("MSFT", db, mock_client)
    assert second.cached is True
    assert second.source == "cache (screen_history)"
    assert second.cache_age_days == 0
    assert second.verdict == "PASS"
    assert second.checks == first.checks
    assert mock_client.screen.await_count == 1  # NOT called again


# ─── Cache MISS — stale row older than TTL is ignored ───────────────────────


@pytest.mark.integration
async def test_stale_row_outside_ttl_triggers_refetch(db) -> None:
    # Seed a stale row (8 days old) directly in the DB
    stale = ScreenHistory(
        id=uuid.uuid4(),
        symbol="RIO",
        screen_date=date.today() - timedelta(days=8),
        ratios_json={"raw_ratios": {}, "checks": {}, "halal_terminal_methodology_verdicts": {}, "as_of_date": None, "failed_checks": []},
        verdict="PASS",
    )
    db.add(stale)
    await db.commit()

    mock_client = _make_mock_client(_compliant_payload("RIO"))
    report = await screen_with_personal_thresholds("RIO", db, mock_client)

    assert report.cached is False
    assert mock_client.screen.await_count == 1  # was called despite stale row


# ─── NOT_COVERED cache HIT ──────────────────────────────────────────────────


@pytest.mark.integration
async def test_not_covered_is_cached(db) -> None:
    mock_client = _make_mock_client(None)
    first = await screen_with_personal_thresholds("ZZZ", db, mock_client)
    assert first.verdict == "NOT_COVERED"

    second = await screen_with_personal_thresholds("ZZZ", db, mock_client)
    assert second.verdict == "NOT_COVERED"
    assert second.cached is True
    assert mock_client.screen.await_count == 1


# ─── Step 1.5 hotfix — Fix A guard against empty/null ratios ────────────────


@pytest.mark.integration
async def test_empty_ratios_object_returns_error_not_pass(db) -> None:
    """Regression: HT returning `{"ratios": {}}` must NOT silently PASS.

    This is the bug that almost shipped to production: a halal gate that
    answers PASS when the upstream gave no usable data is catastrophic.
    """
    mock_client = _make_mock_client({
        "symbol": "ZZZBIDON",
        "overall_status": "compliant",
        "ratios": {},   # ← empty — Fix A trap
        "methodologies": {
            "AAOIFI": {"status": "compliant", "failed_ratios": []},
        },
        "as_of_date": "2026-04-30",
    })
    report = await screen_with_personal_thresholds("ZZZBIDON", db, mock_client)

    assert report.verdict == "ERROR"
    assert report.failed_checks == []
    assert "ratio exploitable" in (report.reason or "").lower() \
        or "ratios" in (report.reason or "").lower()
    assert report.source == "Halal Terminal API (empty ratios payload)"
    # Methodology verdicts are preserved for downstream UI even on ERROR
    assert "AAOIFI" in report.halal_terminal_methodology_verdicts

    # ERROR is NEVER persisted (no cache poisoning)
    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "ZZZBIDON")
    )).scalars().all()
    assert rows == [], "ERROR verdict must not be persisted to screen_history"


@pytest.mark.integration
async def test_all_null_bloquant_ratios_returns_error(db) -> None:
    """The actual pollution shape from production: 4 bloquant ratios = null."""
    mock_client = _make_mock_client({
        "symbol": "NULLS",
        "ratios": {
            "debt_to_marketcap":     None,
            "cash_to_marketcap":     None,
            "impure_revenue_ratio":  None,
            "interest_income_ratio": None,
            "debt_to_assets":        None,
            "receivables_to_assets": None,
        },
        "methodologies": {},
        "as_of_date": "2026-04-30",
    })
    report = await screen_with_personal_thresholds("NULLS", db, mock_client)

    assert report.verdict == "ERROR"
    # Not persisted
    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "NULLS")
    )).scalars().all()
    assert rows == []


@pytest.mark.integration
async def test_partial_ratios_still_passes_per_spec_5_2(db) -> None:
    """Fix A guards on ALL ratios missing, not on partial.

    Spec §5.2 explicitly uses ``ratios.get(field, 0)`` — a single missing
    ratio defaults to 0 (passes its check). Fix A bails ONLY when 0 of 4
    bloquant ratios are present. When 1+ are present, the spec's
    permissive default still applies.
    """
    mock_client = _make_mock_client({
        "symbol": "PARTIAL",
        "ratios": {
            "debt_to_marketcap":     0.02,
            "cash_to_marketcap":     0.05,
            "impure_revenue_ratio":  0.01,
            # interest_income_ratio absent → spec §5.2 defaults to 0
        },
        "methodologies": {},
        "as_of_date": "2026-04-30",
    })
    report = await screen_with_personal_thresholds("PARTIAL", db, mock_client)

    assert report.verdict == "PASS"
    assert report.checks["interest_income_ratio"].value == 0.0
    assert report.checks["interest_income_ratio"].passed is True
    assert report.checks["debt_to_marketcap"].value == pytest.approx(0.02)

    # PASS with at least one real ratio → legitimate, gets cached
    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "PARTIAL")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].verdict == "PASS"
