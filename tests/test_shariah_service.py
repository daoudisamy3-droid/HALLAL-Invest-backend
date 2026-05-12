"""Unit + integration tests for app/services/shariah_service.py (free-tier mode).

Free-tier deviation (see docs/SHARIAH_FREE_TIER_DEVIATION.md): the
service consumes Halal Terminal's aggregate verdict instead of raw
ratios. The 5 cases tested below mirror the contract in that doc.

The HalalTerminalClient is replaced by an AsyncMock so we exercise the
service logic in isolation. The DB is the real Postgres session from
conftest (function-scoped, truncated on teardown).

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


def _aggregate_pass_payload(symbol: str) -> dict[str, Any]:
    """Free-tier Halal Terminal payload — all three flags compliant."""
    return {
        "symbol": symbol,
        "name": f"{symbol} Corp.",
        "is_compliant": True,
        "business_screen_pass": True,
        "business_screen_reason": "Business activity is compliant.",
        "financial_screen_pass": True,
        "shariah_compliance_status": None,
    }


def _aggregate_fail_payload(
    symbol: str,
    *,
    is_compliant: bool | None = False,
    business_screen_pass: bool | None = False,
    business_screen_reason: str | None = "Business activity involves non-halal income.",
    financial_screen_pass: bool | None = True,
) -> dict[str, Any]:
    return {
        "symbol": symbol,
        "is_compliant": is_compliant,
        "business_screen_pass": business_screen_pass,
        "business_screen_reason": business_screen_reason,
        "financial_screen_pass": financial_screen_pass,
        "shariah_compliance_status": None,
    }


def _make_mock_client(payload: Any = None, raises: Exception | None = None) -> AsyncMock:
    client = AsyncMock()
    if raises is not None:
        client.screen.side_effect = raises
    else:
        client.screen.return_value = payload
    return client


# ─── §5.1 thresholds — anti-regression (constants still tracked) ────────────


@pytest.mark.unit
def test_thresholds_match_spec_5_1_strict() -> None:
    """Anti-regression: the 4 bloquant thresholds remain 0.30/0.30/0.03/0.03.

    These constants are dormant in free-tier mode (we consume the
    provider's aggregate verdict) but kept as the canonical record of
    §5.1 — the day we get raw ratios back, the revert is a one-liner.
    """
    assert ShariahCustomThresholds.DEBT_TO_MARKETCAP_MAX == 0.30
    assert ShariahCustomThresholds.CASH_TO_MARKETCAP_MAX == 0.30
    assert ShariahCustomThresholds.IMPURE_REVENUE_MAX == 0.03
    assert ShariahCustomThresholds.INTEREST_INCOME_MAX == 0.03
    assert ShariahCustomThresholds.RECEIVABLES_TO_ASSETS_MAX == 0.45


# ─── CASE 2 — aggregate verdict PASS on 5 reference tickers ─────────────────


@pytest.mark.integration
@pytest.mark.parametrize("symbol", ["AAPL", "MSFT", "RIO", "AIXA.DE", "EOG"])
async def test_aggregate_verdict_pass_for_reference_tickers(db, symbol: str) -> None:
    mock_client = _make_mock_client(_aggregate_pass_payload(symbol))
    report = await screen_with_personal_thresholds(symbol.lower(), db, mock_client)

    assert report.symbol == symbol.upper()
    assert report.verdict == "PASS"
    assert report.failed_checks == []
    # Free-tier: no raw ratios surfaced → no checks built
    assert report.checks == {}
    assert report.halal_terminal_methodology_verdicts == {}
    assert report.source == "Halal Terminal API (aggregate verdict)"
    assert report.cached is False
    mock_client.screen.assert_awaited_once_with(symbol.upper())

    # Persisted (PASS is a stable verdict → cacheable for the TTL window)
    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == symbol.upper())
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].verdict == "PASS"


# ─── CASE 3 — aggregate verdict FAIL on each failure branch ─────────────────


@pytest.mark.integration
async def test_aggregate_verdict_fail_business_screen(db) -> None:
    mock_client = _make_mock_client(_aggregate_fail_payload(
        "BAD_BIZ",
        is_compliant=False,
        business_screen_pass=False,
        business_screen_reason="More than 5% of revenue from alcohol.",
        financial_screen_pass=True,
    ))
    report = await screen_with_personal_thresholds("BAD_BIZ", db, mock_client)

    assert report.verdict == "FAIL"
    assert report.source == "Halal Terminal API (aggregate verdict)"
    assert "alcohol" in (report.reason or "")

    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "BAD_BIZ")
    )).scalars().all()
    assert len(rows) == 1


@pytest.mark.integration
async def test_aggregate_verdict_fail_financial_screen(db) -> None:
    mock_client = _make_mock_client({
        "symbol": "BAD_FIN",
        "is_compliant": False,
        "business_screen_pass": True,
        "business_screen_reason": "Business activity is compliant.",
        "financial_screen_pass": False,
        "financial_screen_reason": "Debt-to-marketcap exceeds AAOIFI 33% cap.",
    })
    report = await screen_with_personal_thresholds("BAD_FIN", db, mock_client)

    assert report.verdict == "FAIL"
    assert "33%" in (report.reason or "") or "Debt" in (report.reason or "")


@pytest.mark.integration
async def test_aggregate_verdict_fail_is_compliant_only(db) -> None:
    """Edge case: is_compliant=false but both sub-screens didn't say which.

    Should still FAIL with a generic reason rather than masking the call.
    """
    mock_client = _make_mock_client({
        "symbol": "AMBIGUOUS_FAIL",
        "is_compliant": False,
        "business_screen_pass": True,
        "financial_screen_pass": True,
    })
    report = await screen_with_personal_thresholds("AMBIGUOUS_FAIL", db, mock_client)

    assert report.verdict == "FAIL"
    assert "non-conforme" in (report.reason or "").lower()


# ─── CASE 1 — ticker_unknown ⇒ NOT_COVERED (NEVER cached) ───────────────────


@pytest.mark.integration
async def test_ticker_unknown_returns_not_covered(db) -> None:
    mock_client = _make_mock_client({
        "symbol": "ZZZBIDON",
        "is_compliant": None,
        "error": "ticker_unknown",
        "error_message": "Symbol 'ZZZBIDON' is not in our universe; no Shariah verdict available.",
    })
    report = await screen_with_personal_thresholds("zzzbidon", db, mock_client)

    assert report.symbol == "ZZZBIDON"
    assert report.verdict == "NOT_COVERED"
    assert report.source == "Halal Terminal API (aggregate verdict)"
    assert "ZZZBIDON" in (report.reason or "")

    # NEW behaviour vs Step 1: NOT_COVERED is NOT persisted
    # (coverage can extend, do not freeze for 7 days)
    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "ZZZBIDON")
    )).scalars().all()
    assert rows == []


@pytest.mark.integration
async def test_not_covered_is_NOT_cached_across_calls(db) -> None:
    """Second NOT_COVERED call must hit upstream again, not the cache."""
    mock_client = _make_mock_client({
        "symbol": "FAKE",
        "is_compliant": None,
        "error": "ticker_unknown",
        "error_message": "Symbol 'FAKE' is not in our universe.",
    })
    first = await screen_with_personal_thresholds("FAKE", db, mock_client)
    second = await screen_with_personal_thresholds("FAKE", db, mock_client)

    assert first.verdict == "NOT_COVERED"
    assert second.verdict == "NOT_COVERED"
    assert second.cached is False
    assert mock_client.screen.await_count == 2  # called twice — no cache hit


# ─── CASE 4 — incomplete response ⇒ ERROR (NOT cached) ──────────────────────


@pytest.mark.integration
async def test_incomplete_response_returns_error(db) -> None:
    mock_client = _make_mock_client({
        "symbol": "WEIRD",
        "name": "Weird Inc.",
        "is_compliant": None,
        "business_screen_pass": None,
        "financial_screen_pass": None,
        # no `error` field — so it's not CASE 1
    })
    report = await screen_with_personal_thresholds("WEIRD", db, mock_client)

    assert report.verdict == "ERROR"
    assert report.source == "Halal Terminal API (error)"
    assert "incomplète" in (report.reason or "").lower()

    # ERROR not persisted
    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "WEIRD")
    )).scalars().all()
    assert rows == []


# ─── CASE 5 — HalalTerminalError ⇒ ERROR (NOT cached) ───────────────────────


@pytest.mark.integration
async def test_halal_terminal_http_error_returns_error(db) -> None:
    mock_client = _make_mock_client(raises=HalalTerminalError("upstream 500"))
    report = await screen_with_personal_thresholds("AAPL", db, mock_client)

    assert report.verdict == "ERROR"
    assert report.source == "Halal Terminal API (error)"
    assert "indisponible" in (report.reason or "").lower()

    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "AAPL")
    )).scalars().all()
    assert rows == []


# ─── Cache layer behaviour ──────────────────────────────────────────────────


@pytest.mark.integration
async def test_cache_miss_persists_pass_row(db) -> None:
    mock_client = _make_mock_client(_aggregate_pass_payload("AAPL"))
    await screen_with_personal_thresholds("AAPL", db, mock_client)

    rows = (await db.execute(
        select(ScreenHistory).where(ScreenHistory.symbol == "AAPL")
    )).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.verdict == "PASS"
    assert row.screen_date == date.today()
    # ratios_json still has the (empty) structural keys for forward-compat
    payload = row.ratios_json
    assert "raw_ratios" in payload
    assert "checks" in payload
    assert payload["checks"] == {}


@pytest.mark.integration
async def test_cache_hit_within_ttl_skips_upstream(db) -> None:
    mock_client = _make_mock_client(_aggregate_pass_payload("MSFT"))

    first = await screen_with_personal_thresholds("MSFT", db, mock_client)
    assert first.cached is False
    assert mock_client.screen.await_count == 1

    second = await screen_with_personal_thresholds("MSFT", db, mock_client)
    assert second.cached is True
    assert second.source == "cache (screen_history)"
    assert second.cache_age_days == 0
    assert second.verdict == "PASS"
    assert mock_client.screen.await_count == 1  # NOT called again


@pytest.mark.integration
async def test_stale_row_outside_ttl_triggers_refetch(db) -> None:
    """A row older than SHARIAH_SCREEN_TTL_DAYS must be ignored."""
    stale = ScreenHistory(
        id=uuid.uuid4(),
        symbol="RIO",
        screen_date=date.today() - timedelta(days=8),
        ratios_json={
            "raw_ratios": {},
            "checks": {},
            "halal_terminal_methodology_verdicts": {},
            "as_of_date": None,
            "failed_checks": [],
            "reason": None,
        },
        verdict="PASS",
    )
    db.add(stale)
    await db.commit()

    mock_client = _make_mock_client(_aggregate_pass_payload("RIO"))
    report = await screen_with_personal_thresholds("RIO", db, mock_client)

    assert report.cached is False
    assert mock_client.screen.await_count == 1
