"""Synthesis service — aggregate the 3 verdict layers (Step 6, §4.4).

Calls Shariah + Investissable + Valuation **in parallel** via
``asyncio.gather``, contains per-layer failures, and combines the three
verdicts into one ``overall_verdict``. Always returns a
``SynthesisReport``, never raises — the endpoint is 200 even when 1 to
3 layers blow up.

Per-layer DB session (Step 7.1 hotfix)
--------------------------------------
SQLAlchemy ``AsyncSession`` is NOT concurrent-safe — sharing one
session across ``asyncio.gather`` branches triggers
``InterfaceError: another operation is in progress`` on the asyncpg
backend. The slowest branch (typically valuation, which hits 3 cache
tables) loses the race and surfaces as ``available=false`` /
``verdict="ERROR"``.

Fix: each gather branch opens its own session from a sessionmaker.
Default sessionmaker is ``AsyncSessionLocal`` (production); tests
override via the ``session_factory`` parameter.

Note on duplicate Shariah call: ``investissable_service`` internally
re-calls ``shariah_service.screen_with_personal_thresholds`` for its
own AAOIFI gate. We accept the redundancy in V1: the second call hits
the 7-day Postgres cache that the first call seeds.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import AsyncSessionLocal
from app.integration.halal_terminal_client import HalalTerminalClient
from app.integration.sec_edgar_client import SecEdgarClient
from app.integration.yfinance_client import YFinanceClient
from app.schemas.investissable import InvestissableReport
from app.schemas.shariah import ShariahReport
from app.schemas.synthesis import (
    OverallVerdict,
    SynthesisLayerStatus,
    SynthesisReport,
)
from app.schemas.valuation import ValuationReport
from app.services import (
    investissable_service,
    shariah_service,
    valuation_service,
)

logger = logging.getLogger(__name__)


# Type alias: a callable returning an async-context-manager that yields
# an AsyncSession. ``AsyncSessionLocal`` matches this shape.
SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


# ─── Public API ─────────────────────────────────────────────────────────────


async def compute_synthesis(
    symbol: str,
    halal_client: HalalTerminalClient,
    sec_client: SecEdgarClient,
    yfinance_client: YFinanceClient,
    *,
    session_factory: SessionFactory | None = None,
) -> SynthesisReport:
    """End-to-end synthesis pipeline. Always 200, never raises.

    Each of the 3 parallel branches acquires its own ``AsyncSession``
    from ``session_factory`` so that concurrent ``db.execute`` calls
    don't collide on a shared session (cf. module docstring).
    """
    sym = symbol.strip().upper()
    factory: SessionFactory = session_factory or AsyncSessionLocal

    async def _halal_branch() -> ShariahReport:
        async with factory() as session:
            return await shariah_service.screen_with_personal_thresholds(
                sym, session, halal_client
            )

    async def _inv_branch() -> InvestissableReport:
        async with factory() as session:
            return await investissable_service.compute_investissable(
                sym, session, halal_client, sec_client
            )

    async def _val_branch() -> ValuationReport:
        async with factory() as session:
            return await valuation_service.compute_valuation(
                sym, session, sec_client, yfinance_client=yfinance_client
            )

    halal_res, inv_res, val_res = await asyncio.gather(
        _safe(_halal_branch(), "shariah"),
        _safe(_inv_branch(), "investissable"),
        _safe(_val_branch(), "valuation"),
        return_exceptions=False,
    )

    return _aggregate(sym, halal_res, inv_res, val_res)


# ─── Safe wrapper ───────────────────────────────────────────────────────────


async def _safe(coro, layer_name: str):
    """Run ``coro`` and return either its result or a short error string.

    Returns ``(report, None)`` on success and ``(None, "short err")`` on
    failure. Never raises.
    """
    try:
        result = await coro
        return (result, None)
    except Exception as exc:  # noqa: BLE001 — catch-all is intentional
        logger.warning("synthesis layer=%s raised err=%s", layer_name, exc)
        return (None, f"{type(exc).__name__}: {exc}"[:200])


# ─── Aggregation ────────────────────────────────────────────────────────────


def _aggregate(
    symbol: str,
    halal_res: tuple[ShariahReport | None, str | None],
    inv_res: tuple[InvestissableReport | None, str | None],
    val_res: tuple[ValuationReport | None, str | None],
) -> SynthesisReport:
    halal_report, halal_err = halal_res
    inv_report, inv_err = inv_res
    val_report, val_err = val_res

    halal_layer = _layer_for_shariah(halal_report, halal_err)
    inv_layer = _layer_for_investissable(inv_report, inv_err)
    val_layer = _layer_for_valuation(val_report, val_err)

    errors: list[str] = []
    if halal_err:
        errors.append(f"halal: {halal_err}")
    if inv_err:
        errors.append(f"investissable: {inv_err}")
    if val_err:
        errors.append(f"valuation: {val_err}")

    warnings: list[str] = []
    if halal_report is not None and halal_report.verdict == "ERROR":
        warnings.append(
            "Halal Terminal indisponible — vérification éthique impossible "
            "(verdict global REQUIRES_REVIEW)."
        )
    if halal_report is not None and halal_report.verdict == "NOT_COVERED":
        warnings.append(
            "Ticker non couvert par Halal Terminal — pas de filtrage éthique "
            "automatique (verdict global REQUIRES_REVIEW)."
        )
    if inv_report is not None and inv_report.verdict == "INCERTAIN":
        warnings.append(
            "Investissable INCERTAIN — données SEC manquantes ou ticker "
            "non-US."
        )
    if val_report is not None and val_report.verdict == "INDÉTERMINÉ":
        warnings.append(
            "Valuation INDÉTERMINÉ — < 2 méthodes disponibles ou current_price "
            "absent."
        )

    overall_verdict, overall_label = _overall(
        halal_layer, inv_layer, val_layer, has_errors=bool(errors)
    )

    return SynthesisReport(
        symbol=symbol,
        overall_verdict=overall_verdict,
        overall_label=overall_label,
        halal=halal_layer,
        investissable=inv_layer,
        valuation=val_layer,
        warnings=warnings,
        errors=errors,
        computed_at=datetime.now(tz=timezone.utc),
    )


# ─── Layer extractors ───────────────────────────────────────────────────────


def _layer_for_shariah(
    report: ShariahReport | None, err: str | None
) -> SynthesisLayerStatus:
    if report is None:
        return SynthesisLayerStatus(
            verdict="ERROR", label=None, available=False, error=err
        )
    return SynthesisLayerStatus(
        verdict=report.verdict,
        label=None,  # ShariahReport doesn't expose a verbose label
        available=True,
        error=None,
    )


def _layer_for_investissable(
    report: InvestissableReport | None, err: str | None
) -> SynthesisLayerStatus:
    if report is None:
        return SynthesisLayerStatus(
            verdict="ERROR", label=None, available=False, error=err
        )
    return SynthesisLayerStatus(
        verdict=report.verdict,
        label=report.label,
        available=True,
        error=None,
    )


def _layer_for_valuation(
    report: ValuationReport | None, err: str | None
) -> SynthesisLayerStatus:
    if report is None:
        return SynthesisLayerStatus(
            verdict="ERROR", label=None, available=False, error=err
        )
    return SynthesisLayerStatus(
        verdict=report.verdict,
        label=report.label,
        available=True,
        error=None,
    )


# ─── Overall verdict cascade ────────────────────────────────────────────────


def _overall(
    halal: SynthesisLayerStatus,
    inv: SynthesisLayerStatus,
    val: SynthesisLayerStatus,
    *,
    has_errors: bool,
) -> tuple[OverallVerdict, str]:
    """Cascade rule (Step 6 plan validated)."""
    # Halal layer drives the cascade.
    if halal.verdict == "FAIL":
        return "BLOCKED", "NON HALAL — bloqué au filtrage éthique"

    if halal.verdict in ("ERROR", "NOT_COVERED") or not halal.available:
        return "REQUIRES_REVIEW", "À VÉRIFIER MANUELLEMENT"

    # halal.verdict == "PASS" from here onward.

    if not inv.available:
        return "REQUIRES_REVIEW", "À VÉRIFIER MANUELLEMENT"

    if inv.verdict == "NON":
        return "NOT_INVESTABLE", f"NON INVESTISSABLE — {inv.label or 'non éligible'}"

    if inv.verdict == "INCERTAIN":
        return "REQUIRES_REVIEW", "À VÉRIFIER MANUELLEMENT"

    # inv.verdict in {"OUI", "OUI_NEUTRE"} from here.

    if not val.available:
        return "REQUIRES_REVIEW", "À VÉRIFIER MANUELLEMENT"

    if val.verdict in ("NON", "INDÉTERMINÉ"):
        return "REQUIRES_REVIEW", "À VÉRIFIER MANUELLEMENT"

    # Everything green.
    parts = ["INVESTISSABLE"]
    if inv.label:
        parts.append(inv.label)
    if val.label:
        parts.append(val.label)
    return "INVESTABLE", " — ".join(parts)
