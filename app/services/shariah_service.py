"""Shariah screening service — applies §5.1 strict personal thresholds.

Spec authority:
  - §3.2.1  Halal Terminal raw_ratios shape
  - §5.1    ShariahCustomThresholds (strict personal, NON-NEGOCIABLES :
              0.30 / 0.30 / 0.03 / 0.03 / 0.45)
  - §5.2    screen_with_personal_thresholds algorithm
  - §1.3 Principe 2 — AAOIFI is a bloquant gate, not a score
  - master-prompt §5.3 — Halal Terminal has NO fallback; API down ⇒
    verdict "ERROR" ⇒ blocking new entries downstream.

Cache layer: validated Q1 of the Step 1 plan — we use the
`screen_history` table itself as a 7-day cache (TTL =
settings.SHARIAH_SCREEN_TTL_DAYS). No Redis. The composite DESC index
ix_screen_history_symbol_screen_date_desc (alembic 002) keeps the
lookup ≤ a few ms.

Strict §5.2: only 4 bloquant checks (debt_to_marketcap,
cash_to_marketcap, impure_revenue_ratio, interest_income_ratio).
debt_to_assets and receivables_to_assets are exposed in raw_ratios for
information but DO NOT participate in the verdict (validated Q5).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import HalalTerminalError
from app.integration.halal_terminal_client import HalalTerminalClient
from app.models.screen_history import ScreenHistory
from app.schemas.shariah import (
    CheckName,
    MethodologyVerdict,
    ShariahCheck,
    ShariahRatios,
    ShariahReport,
)

logger = logging.getLogger(__name__)


# ─── §5.1 personal strict thresholds (single source of truth: settings) ──────


class ShariahCustomThresholds:
    """View object exposing the §5.1 thresholds.

    Values come from `settings.SHARIAH_*` so we have a single source of
    truth (config.py reflects env vars). The class form preserves
    traceability to the spec snippet in §5.1.
    """

    DEBT_TO_MARKETCAP_MAX: float = settings.SHARIAH_DEBT_TO_MARKETCAP_MAX
    DEBT_TO_ASSETS_MAX: float = settings.SHARIAH_DEBT_TO_MARKETCAP_MAX  # alt methodo, info-only
    CASH_TO_MARKETCAP_MAX: float = settings.SHARIAH_CASH_TO_MARKETCAP_MAX
    IMPURE_REVENUE_MAX: float = settings.SHARIAH_IMPURE_REVENUE_MAX
    INTEREST_INCOME_MAX: float = settings.SHARIAH_INTEREST_INCOME_MAX
    RECEIVABLES_TO_ASSETS_MAX: float = settings.SHARIAH_RECEIVABLES_TO_ASSETS_MAX  # info-only


# ─── Public API ──────────────────────────────────────────────────────────────


async def screen_with_personal_thresholds(
    symbol: str,
    db: AsyncSession,
    client: HalalTerminalClient,
) -> ShariahReport:
    """Resolve the Shariah report for `symbol`, using cache when fresh.

    Implements §5.2 verbatim for the algorithm, plus a `screen_history`
    cache layer (Q1 plan decision).
    """
    sym = symbol.strip().upper()

    cached = await _read_cache(db, sym)
    if cached is not None:
        return cached

    try:
        upstream = await client.screen(sym)
    except HalalTerminalError as exc:
        logger.warning("shariah_screen ERROR symbol=%s err=%s", sym, exc)
        return ShariahReport(
            symbol=sym,
            verdict="ERROR",
            source="Halal Terminal API (error)",
            reason=f"Halal Terminal indisponible: {exc}",
        )

    if upstream is None:
        report = ShariahReport(
            symbol=sym,
            verdict="NOT_COVERED",
            source="Halal Terminal API + custom thresholds",
            reason=f"{sym} non couvert par Halal Terminal",
        )
        await _persist(db, sym, report, ratios_payload={})
        return report

    report = _build_report_from_upstream(sym, upstream)
    await _persist(db, sym, report, ratios_payload=_serialize_for_db(report))
    return report


# ─── Internals ───────────────────────────────────────────────────────────────


_BLOCKING_CHECKS: tuple[tuple[CheckName, str, float], ...] = (
    # (check_name, raw_ratio_field, threshold)
    ("debt_to_marketcap",      "debt_to_marketcap",       ShariahCustomThresholds.DEBT_TO_MARKETCAP_MAX),
    ("cash_to_marketcap",      "cash_to_marketcap",       ShariahCustomThresholds.CASH_TO_MARKETCAP_MAX),
    ("impure_revenue_ratio",   "impure_revenue_ratio",    ShariahCustomThresholds.IMPURE_REVENUE_MAX),
    ("interest_income_ratio",  "interest_income_ratio",   ShariahCustomThresholds.INTEREST_INCOME_MAX),
)


def _is_present_numeric(value: Any) -> bool:
    """True iff `value` is a usable numeric ratio (not None, not bool, not absent)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _build_report_from_upstream(symbol: str, upstream: dict[str, Any]) -> ShariahReport:
    """Apply §5.2 logic to the parsed upstream payload.

    Robustness guard (Step 1.5 hotfix): §5.2 specifies
    ``ratios.get(field, 0)`` — a permissive default that lets a *single*
    missing ratio fall through as 0 (and therefore pass the check).
    That default is appropriate when at least *some* ratios are
    present. It becomes catastrophic when the *entire* payload is empty
    or all-null: 0.0 ≤ 0.30 for every check ⇒ verdict PASS for any
    ticker, including non-existent ones. A halal gate that emits PASS
    on no data is worse than useless.

    Guard: if NONE of the 4 bloquant ratios is present and numeric,
    bail out with verdict ERROR. The frontend surfaces the upstream
    failure clearly; the cache layer refuses to persist ERROR.
    """
    raw_ratios_dict = upstream.get("ratios") or {}
    if not isinstance(raw_ratios_dict, dict):
        raw_ratios_dict = {}

    present_count = sum(
        1
        for _, field, _ in _BLOCKING_CHECKS
        if _is_present_numeric(raw_ratios_dict.get(field))
    )

    ratios = ShariahRatios.model_validate(raw_ratios_dict)
    methodology_verdicts = {
        name: MethodologyVerdict.model_validate(payload)
        for name, payload in (upstream.get("methodologies") or {}).items()
    }

    if present_count == 0:
        logger.warning(
            "shariah_service refusing PASS for symbol=%s — upstream payload has "
            "no usable bloquant ratio (raw_ratios keys=%s)",
            symbol,
            sorted(raw_ratios_dict.keys()) if raw_ratios_dict else [],
        )
        return ShariahReport(
            symbol=symbol,
            verdict="ERROR",
            source="Halal Terminal API (empty ratios payload)",
            reason=(
                "Halal Terminal a renvoyé une réponse sans aucun ratio exploitable "
                "(0/4 des ratios bloquants présents). Verdict bloqué par sécurité — "
                "la conformité ne peut pas être affirmée à partir de données absentes."
            ),
            raw_ratios=ratios,
            halal_terminal_methodology_verdicts=methodology_verdicts,
        )

    checks: dict[CheckName, ShariahCheck] = {}
    failed: list[CheckName] = []

    for check_name, field, threshold in _BLOCKING_CHECKS:
        # §5.2: missing ratio defaults to 0 (cannot fail a check we can't compute).
        # The aggregate guard above ensures at least one ratio is real before we
        # reach this loop, so this permissive default is safe.
        value = raw_ratios_dict.get(field, 0.0)
        if value is None:
            value = 0.0
        passed = value <= threshold
        checks[check_name] = ShariahCheck(value=float(value), threshold=threshold, **{"pass": passed})
        if not passed:
            failed.append(check_name)

    as_of_raw = upstream.get("as_of_date")
    as_of_parsed: date | None = None
    if isinstance(as_of_raw, str):
        try:
            as_of_parsed = date.fromisoformat(as_of_raw)
        except ValueError:
            logger.warning("halal_terminal as_of_date unparseable: %r", as_of_raw)

    return ShariahReport(
        symbol=symbol,
        verdict="PASS" if not failed else "FAIL",
        failed_checks=failed,
        checks=checks,
        halal_terminal_methodology_verdicts=methodology_verdicts,
        raw_ratios=ratios,
        as_of_date=as_of_parsed,
        source="Halal Terminal API + custom thresholds",
    )


# ─── Cache layer (screen_history table) ──────────────────────────────────────


async def _read_cache(db: AsyncSession, symbol: str) -> ShariahReport | None:
    cutoff = date.today() - timedelta(days=settings.SHARIAH_SCREEN_TTL_DAYS)
    stmt = (
        select(ScreenHistory)
        .where(ScreenHistory.symbol == symbol)
        .where(ScreenHistory.screen_date >= cutoff)
        .order_by(ScreenHistory.screen_date.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None

    age_days = (date.today() - row.screen_date).days
    payload = row.ratios_json or {}

    try:
        return _deserialize_from_db(symbol, row.verdict, row.screen_date, age_days, payload)
    except Exception as exc:  # corrupt cache row — log + ignore so we re-fetch
        logger.warning(
            "shariah_screen cache row deserialize failed symbol=%s err=%s — refetching",
            symbol, exc,
        )
        return None


def _deserialize_from_db(
    symbol: str,
    verdict: str,
    screen_date: date,
    age_days: int,
    payload: dict[str, Any],
) -> ShariahReport:
    if verdict == "NOT_COVERED":
        return ShariahReport(
            symbol=symbol,
            verdict="NOT_COVERED",
            source="cache (screen_history)",
            cached=True,
            cache_age_days=age_days,
            reason=f"{symbol} non couvert par Halal Terminal",
        )

    raw_ratios = ShariahRatios.model_validate(payload.get("raw_ratios", {}))
    checks_raw = payload.get("checks", {})
    checks: dict[CheckName, ShariahCheck] = {
        k: ShariahCheck.model_validate(v) for k, v in checks_raw.items()
    }
    methodology_verdicts = {
        name: MethodologyVerdict.model_validate(pay)
        for name, pay in (payload.get("halal_terminal_methodology_verdicts") or {}).items()
    }
    as_of_raw = payload.get("as_of_date")
    as_of_parsed: date | None = None
    if isinstance(as_of_raw, str):
        try:
            as_of_parsed = date.fromisoformat(as_of_raw)
        except ValueError:
            as_of_parsed = None

    return ShariahReport(
        symbol=symbol,
        verdict=verdict,  # type: ignore[arg-type]
        failed_checks=list(payload.get("failed_checks") or []),
        checks=checks,
        halal_terminal_methodology_verdicts=methodology_verdicts,
        raw_ratios=raw_ratios,
        as_of_date=as_of_parsed,
        source="cache (screen_history)",
        cached=True,
        cache_age_days=age_days,
    )


async def _persist(
    db: AsyncSession,
    symbol: str,
    report: ShariahReport,
    ratios_payload: dict[str, Any],
) -> None:
    """Insert a screen_history row. Errors are logged, not propagated.

    A failed insert means the cache will miss next time — acceptable
    degradation. The verdict is already in the report we return.
    """
    if report.verdict == "ERROR":
        return  # §3.4 — never persist transient upstream failures
    try:
        row = ScreenHistory(
            id=uuid.uuid4(),
            symbol=symbol,
            screen_date=date.today(),
            ratios_json=ratios_payload,
            verdict=report.verdict,
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        logger.warning("shariah_screen persist failed symbol=%s err=%s", symbol, exc)
        await db.rollback()


def _serialize_for_db(report: ShariahReport) -> dict[str, Any]:
    return {
        "raw_ratios": report.raw_ratios.model_dump(),
        "checks": {k: v.model_dump(by_alias=True) for k, v in report.checks.items()},
        "halal_terminal_methodology_verdicts": {
            k: v.model_dump() for k, v in report.halal_terminal_methodology_verdicts.items()
        },
        "as_of_date": report.as_of_date.isoformat() if report.as_of_date else None,
        "failed_checks": list(report.failed_checks),
    }
