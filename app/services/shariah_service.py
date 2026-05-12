"""Shariah screening service — free-tier Halal Terminal mode.

Spec authority and free-tier deviation:
  - §5.1 / §5.2 of finterminal-spec.md describe custom strict thresholds
    (0.30 / 0.30 / 0.03 / 0.03) to apply on raw ratios returned by the
    provider. The premium tier of Halal Terminal would surface those
    ratios; the free tier — which we use in V1 — does not.
  - We therefore consume the provider's **aggregate verdict** instead
    (``is_compliant`` / ``business_screen_pass`` / ``financial_screen_pass``)
    and map it onto our 4-state ShariahReport contract.
  - Full rationale and plan-to-revert: ``docs/SHARIAH_FREE_TIER_DEVIATION.md``.
  - §1.3 Principe 2 — AAOIFI remains a bloquant gate even in this mode.
  - master-prompt §5.3 — Halal Terminal has NO fallback; API failure ⇒
    verdict "ERROR" ⇒ downstream refuses new entries.

The ``ShariahCustomThresholds`` class below is kept (dormant) as the
canonical record of §5.1 — we will reactivate it the day raw ratios
become available again.

Cache layer: validated Q1 of the Step 1 plan — we use the
``screen_history`` table itself as a 7-day cache (TTL =
``settings.SHARIAH_SCREEN_TTL_DAYS``). NOT_COVERED is NOT cached
(coverage can extend); ERROR is NOT cached (transient).
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


# ─── §5.1 personal strict thresholds (DORMANT in free-tier mode) ─────────────


class ShariahCustomThresholds:
    """View object exposing the §5.1 thresholds (free-tier-dormant).

    Values come from ``settings.SHARIAH_*`` so we have a single source of
    truth. In free-tier mode these thresholds are NOT applied (we
    consume the provider's aggregate verdict instead). The class is
    kept to preserve traceability to the spec and to make the future
    revert trivial when raw ratios become available again.
    """

    DEBT_TO_MARKETCAP_MAX: float = settings.SHARIAH_DEBT_TO_MARKETCAP_MAX
    DEBT_TO_ASSETS_MAX: float = settings.SHARIAH_DEBT_TO_MARKETCAP_MAX  # alt methodo, info-only
    CASH_TO_MARKETCAP_MAX: float = settings.SHARIAH_CASH_TO_MARKETCAP_MAX
    IMPURE_REVENUE_MAX: float = settings.SHARIAH_IMPURE_REVENUE_MAX
    INTEREST_INCOME_MAX: float = settings.SHARIAH_INTEREST_INCOME_MAX
    RECEIVABLES_TO_ASSETS_MAX: float = settings.SHARIAH_RECEIVABLES_TO_ASSETS_MAX  # info-only


# ─── Source strings (Literal aligned with schema) ────────────────────────────


_SOURCE_AGGREGATE = "Halal Terminal API (aggregate verdict)"
_SOURCE_ERROR = "Halal Terminal API (error)"
_SOURCE_CACHE = "cache (screen_history)"


# ─── Public API ──────────────────────────────────────────────────────────────


async def screen_with_personal_thresholds(
    symbol: str,
    db: AsyncSession,
    client: HalalTerminalClient,
) -> ShariahReport:
    """Resolve the Shariah report for ``symbol``, using cache when fresh.

    Free-tier mapping — see module docstring for full rationale:
      CASE 1: provider returns ``error=ticker_unknown``  ⇒ NOT_COVERED (no cache)
      CASE 2: aggregate verdict all ``true``             ⇒ PASS (cached)
      CASE 3: aggregate verdict at least one ``false``   ⇒ FAIL (cached)
      CASE 4: aggregate fields all null, no error        ⇒ ERROR (no cache)
      CASE 5: HTTP / timeout / network failure           ⇒ ERROR (no cache)
    """
    sym = symbol.strip().upper()

    cached = await _read_cache(db, sym)
    if cached is not None:
        return cached

    try:
        upstream = await client.screen(sym)
    except HalalTerminalError as exc:
        # CASE 5
        logger.warning("shariah_screen ERROR symbol=%s err=%s", sym, exc)
        return ShariahReport(
            symbol=sym,
            verdict="ERROR",
            source=_SOURCE_ERROR,
            reason=f"Halal Terminal indisponible: {exc}",
        )

    if upstream is None:
        # Provider responded with a clean 404 (rare on free tier — usually returns
        # 200 with ``error=ticker_unknown``). Treat as coverage gap.
        return ShariahReport(
            symbol=sym,
            verdict="NOT_COVERED",
            source=_SOURCE_AGGREGATE,
            reason=f"{sym} non couvert par Halal Terminal",
        )

    report = _build_report_from_upstream(sym, upstream)
    await _persist(db, sym, report)
    return report


# ─── Aggregate-verdict mapping (free-tier) ───────────────────────────────────


def _build_report_from_upstream(symbol: str, upstream: dict[str, Any]) -> ShariahReport:
    """Apply free-tier aggregate-verdict logic.

    The 4 cases handled here mirror the contract in
    ``docs/SHARIAH_FREE_TIER_DEVIATION.md``. The 5th case (HTTP/network
    failure) is handled by the caller before reaching this function.
    """
    # CASE 1 — coverage gap on free tier (200 with explicit error field)
    if upstream.get("error") == "ticker_unknown":
        msg = upstream.get("error_message") or f"{symbol} non couvert par Halal Terminal"
        return ShariahReport(
            symbol=symbol,
            verdict="NOT_COVERED",
            source=_SOURCE_AGGREGATE,
            reason=str(msg),
        )

    is_compliant = upstream.get("is_compliant")
    business_pass = upstream.get("business_screen_pass")
    financial_pass = upstream.get("financial_screen_pass")

    # CASE 4 — incomplete response (all 3 null AND no error). Refuse to commit.
    if is_compliant is None and business_pass is None and financial_pass is None:
        logger.warning(
            "shariah_screen incomplete upstream symbol=%s payload_keys=%s",
            symbol, sorted(upstream.keys()),
        )
        return ShariahReport(
            symbol=symbol,
            verdict="ERROR",
            source=_SOURCE_ERROR,
            reason=(
                "Réponse Halal Terminal incomplète (is_compliant, "
                "business_screen_pass et financial_screen_pass sont tous null) — "
                "conformité indéterminable."
            ),
        )

    # CASE 3 — at least one screen failed ⇒ FAIL with a human-readable reason
    if is_compliant is False or business_pass is False or financial_pass is False:
        if business_pass is False:
            reason = str(
                upstream.get("business_screen_reason")
                or "Échec du business screen Halal Terminal."
            )
        elif financial_pass is False:
            reason = str(
                upstream.get("financial_screen_reason")
                or "Échec du financial screen Halal Terminal."
            )
        else:
            # is_compliant=False but the two sub-screens didn't say which.
            reason = "Provider a évalué le ticker comme non-conforme (is_compliant=false)."

        return ShariahReport(
            symbol=symbol,
            verdict="FAIL",
            source=_SOURCE_AGGREGATE,
            reason=reason,
        )

    # CASE 2 — all aggregate verdicts pass ⇒ PASS
    if is_compliant is True and business_pass is True and financial_pass is True:
        # Optional contextual reason (free-tier may carry a sentence)
        msg = upstream.get("business_screen_reason")
        return ShariahReport(
            symbol=symbol,
            verdict="PASS",
            source=_SOURCE_AGGREGATE,
            reason=str(msg) if isinstance(msg, str) else None,
        )

    # Defensive catch-all: at this point at least one flag is non-None but the
    # combination doesn't match PASS or FAIL (e.g., one True, two Nones). Refuse.
    logger.warning(
        "shariah_screen ambiguous upstream symbol=%s is_compliant=%r "
        "business_pass=%r financial_pass=%r",
        symbol, is_compliant, business_pass, financial_pass,
    )
    return ShariahReport(
        symbol=symbol,
        verdict="ERROR",
        source=_SOURCE_ERROR,
        reason=(
            "Réponse Halal Terminal ambiguë : "
            f"is_compliant={is_compliant}, "
            f"business_screen_pass={business_pass}, "
            f"financial_screen_pass={financial_pass}."
        ),
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
        return _deserialize_from_db(symbol, row.verdict, age_days, payload)
    except Exception as exc:  # corrupt cache row — log + ignore so we re-fetch
        logger.warning(
            "shariah_screen cache row deserialize failed symbol=%s err=%s — refetching",
            symbol, exc,
        )
        return None


def _deserialize_from_db(
    symbol: str,
    verdict: str,
    age_days: int,
    payload: dict[str, Any],
) -> ShariahReport:
    """Reconstruct a ShariahReport from a screen_history row.

    Backward compatible with the pre-free-tier shape (which had populated
    ``checks`` and ``raw_ratios``) — we still hydrate those fields if
    present so any stale-but-non-toxic rows remain renderable.
    """
    raw_ratios = ShariahRatios.model_validate(payload.get("raw_ratios", {}) or {})
    checks_raw = payload.get("checks", {}) or {}
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

    reason = payload.get("reason")
    return ShariahReport(
        symbol=symbol,
        verdict=verdict,  # type: ignore[arg-type]
        failed_checks=list(payload.get("failed_checks") or []),
        checks=checks,
        halal_terminal_methodology_verdicts=methodology_verdicts,
        raw_ratios=raw_ratios,
        as_of_date=as_of_parsed,
        source=_SOURCE_CACHE,
        cached=True,
        cache_age_days=age_days,
        reason=str(reason) if isinstance(reason, str) else None,
    )


async def _persist(db: AsyncSession, symbol: str, report: ShariahReport) -> None:
    """Insert a screen_history row. Skips verdicts that should never cache.

    - ERROR        : transient upstream failure, retry next time
    - NOT_COVERED  : provider coverage can extend; don't freeze for 7 days

    A failed insert is logged but never propagated — the report is
    already returned to the caller.
    """
    if report.verdict in ("ERROR", "NOT_COVERED"):
        return

    payload = _serialize_for_db(report)
    try:
        row = ScreenHistory(
            id=uuid.uuid4(),
            symbol=symbol,
            screen_date=date.today(),
            ratios_json=payload,
            verdict=report.verdict,
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        logger.warning("shariah_screen persist failed symbol=%s err=%s", symbol, exc)
        await db.rollback()


def _serialize_for_db(report: ShariahReport) -> dict[str, Any]:
    """Serialize a ShariahReport for ``ratios_json`` storage.

    In free-tier mode ``checks`` / ``raw_ratios`` / ``halal_terminal_methodology_verdicts``
    are empty by design — we still persist the structure so the
    deserialiser doesn't need to special-case the absence.
    """
    return {
        "raw_ratios": report.raw_ratios.model_dump(),
        "checks": {k: v.model_dump(by_alias=True) for k, v in report.checks.items()},
        "halal_terminal_methodology_verdicts": {
            k: v.model_dump() for k, v in report.halal_terminal_methodology_verdicts.items()
        },
        "as_of_date": report.as_of_date.isoformat() if report.as_of_date else None,
        "failed_checks": list(report.failed_checks),
        "reason": report.reason,
    }
