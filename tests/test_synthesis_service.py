"""Tests for ``app/services/synthesis_service.py`` (Step 6).

Each layer (shariah / investissable / valuation) is replaced by a
monkey-patched coroutine returning a controlled report — or raising an
exception, to exercise the per-layer error containment.

Verdict cascade coverage:
  - all-green               → INVESTABLE
  - halal FAIL              → BLOCKED
  - halal ERROR             → REQUIRES_REVIEW
  - halal NOT_COVERED       → REQUIRES_REVIEW
  - investissable NON       → NOT_INVESTABLE
  - investissable INCERTAIN → REQUIRES_REVIEW
  - valuation NON           → REQUIRES_REVIEW (cascade)
  - valuation INDÉTERMINÉ   → REQUIRES_REVIEW
  - 3 layers raise          → REQUIRES_REVIEW + errors[]
  - parallel timing         → total ≈ slowest, not sum
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from app.schemas.investissable import InvestissableReport
from app.schemas.shariah import ShariahReport
from app.schemas.valuation import ValuationReport
from app.services import synthesis_service as ss


def _factory_yielding(session):
    """Build a ``session_factory`` test helper that yields the test ``db`` fixture.

    The real ``AsyncSessionLocal`` would connect to the prod URL on every
    parallel branch — undesirable in tests where the 3 layers are
    monkeypatched anyway. This factory just hands back the test session
    inside an async context manager.
    """
    @asynccontextmanager
    async def _cm():
        yield session
    return _cm


# ─── Fixture builders ───────────────────────────────────────────────────────


def _halal(verdict: str = "PASS") -> ShariahReport:
    return ShariahReport(
        symbol="AAPL",
        verdict=verdict,  # type: ignore[arg-type]
        source="Halal Terminal API (aggregate verdict)",
        reason=None,
    )


def _inv(verdict: str = "OUI", label: str | None = "QUALITÉ EXCELLENTE") -> InvestissableReport:
    return InvestissableReport(
        symbol="AAPL",
        verdict=verdict,  # type: ignore[arg-type]
        label=label,
        quality_score=72.0 if verdict == "OUI" else None,
        data_completeness="FULL" if verdict == "OUI" else "INSUFFICIENT",
    )


def _val(verdict: str = "OUI_NEUTRE", label: str | None = "JUSTE PRIX") -> ValuationReport:
    return ValuationReport(
        symbol="AAPL",
        verdict=verdict,  # type: ignore[arg-type]
        label=label,
        confidence="N/A",
        n_methods=0,
    )


def _patch_layers(monkeypatch, *, halal, inv, val) -> None:
    """Replace the 3 service calls. Each arg is either a Report or an Exception."""

    async def halal_co(*_a, **_kw):
        if isinstance(halal, Exception):
            raise halal
        return halal

    async def inv_co(*_a, **_kw):
        if isinstance(inv, Exception):
            raise inv
        return inv

    async def val_co(*_a, **_kw):
        if isinstance(val, Exception):
            raise val
        return val

    monkeypatch.setattr(
        ss.shariah_service, "screen_with_personal_thresholds", halal_co
    )
    monkeypatch.setattr(
        ss.investissable_service, "compute_investissable", inv_co
    )
    monkeypatch.setattr(
        ss.valuation_service, "compute_valuation", val_co
    )


# ─── Cascade ────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_all_green_yields_investable(db, monkeypatch) -> None:
    _patch_layers(
        monkeypatch,
        halal=_halal("PASS"),
        inv=_inv("OUI", "QUALITÉ EXCELLENTE"),
        val=_val("OUI", "SOUS-ÉVALUÉE"),
    )
    out = await ss.compute_synthesis("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    assert out.overall_verdict == "INVESTABLE"
    assert out.overall_label == "INVESTISSABLE — QUALITÉ EXCELLENTE — SOUS-ÉVALUÉE"
    assert out.errors == []
    assert out.halal.available is True
    assert out.halal.verdict == "PASS"


@pytest.mark.integration
async def test_halal_fail_short_circuits_to_blocked(db, monkeypatch) -> None:
    _patch_layers(
        monkeypatch,
        halal=_halal("FAIL"),
        inv=_inv("OUI"),
        val=_val("OUI"),
    )
    out = await ss.compute_synthesis("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    assert out.overall_verdict == "BLOCKED"
    assert "NON HALAL" in out.overall_label
    assert out.halal.verdict == "FAIL"


@pytest.mark.integration
async def test_halal_error_triggers_requires_review(db, monkeypatch) -> None:
    _patch_layers(
        monkeypatch,
        halal=_halal("ERROR"),
        inv=_inv("OUI"),
        val=_val("OUI"),
    )
    out = await ss.compute_synthesis("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    assert out.overall_verdict == "REQUIRES_REVIEW"
    assert any("Halal Terminal indisponible" in w for w in out.warnings)


@pytest.mark.integration
async def test_halal_not_covered_triggers_requires_review(db, monkeypatch) -> None:
    _patch_layers(
        monkeypatch,
        halal=_halal("NOT_COVERED"),
        inv=_inv("OUI"),
        val=_val("OUI"),
    )
    out = await ss.compute_synthesis("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    assert out.overall_verdict == "REQUIRES_REVIEW"
    assert any("non couvert" in w for w in out.warnings)


@pytest.mark.integration
async def test_investissable_non_yields_not_investable(db, monkeypatch) -> None:
    _patch_layers(
        monkeypatch,
        halal=_halal("PASS"),
        inv=_inv("NON", "QUALITÉ INSUFFISANTE"),
        val=_val("OUI"),
    )
    out = await ss.compute_synthesis("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    assert out.overall_verdict == "NOT_INVESTABLE"
    assert "NON INVESTISSABLE" in out.overall_label
    assert "QUALITÉ INSUFFISANTE" in out.overall_label


@pytest.mark.integration
async def test_investissable_incertain_yields_requires_review(db, monkeypatch) -> None:
    _patch_layers(
        monkeypatch,
        halal=_halal("PASS"),
        inv=_inv("INCERTAIN", None),
        val=_val("OUI"),
    )
    out = await ss.compute_synthesis("AIXA.DE", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    assert out.overall_verdict == "REQUIRES_REVIEW"
    assert any("INCERTAIN" in w for w in out.warnings)


@pytest.mark.integration
async def test_valuation_non_yields_requires_review(db, monkeypatch) -> None:
    """OUI investissable + NON valuation → REQUIRES_REVIEW (overpriced but quality OK)."""
    _patch_layers(
        monkeypatch,
        halal=_halal("PASS"),
        inv=_inv("OUI"),
        val=_val("NON", "SURÉVALUÉE"),
    )
    out = await ss.compute_synthesis("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    assert out.overall_verdict == "REQUIRES_REVIEW"


@pytest.mark.integration
async def test_valuation_indetermine_yields_requires_review(db, monkeypatch) -> None:
    _patch_layers(
        monkeypatch,
        halal=_halal("PASS"),
        inv=_inv("OUI"),
        val=_val("INDÉTERMINÉ", None),
    )
    out = await ss.compute_synthesis("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    assert out.overall_verdict == "REQUIRES_REVIEW"
    assert any("INDÉTERMINÉ" in w for w in out.warnings)


# ─── Per-layer error containment ────────────────────────────────────────────


@pytest.mark.integration
async def test_single_layer_exception_is_contained(db, monkeypatch) -> None:
    """Valuation crashes → other layers still surface, status is REQUIRES_REVIEW."""
    _patch_layers(
        monkeypatch,
        halal=_halal("PASS"),
        inv=_inv("OUI"),
        val=RuntimeError("yfinance down"),
    )
    out = await ss.compute_synthesis("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    assert out.overall_verdict == "REQUIRES_REVIEW"
    assert out.halal.available is True
    assert out.investissable.available is True
    assert out.valuation.available is False
    assert out.valuation.verdict == "ERROR"
    assert out.valuation.error and "yfinance down" in out.valuation.error
    assert any("valuation" in e for e in out.errors)


@pytest.mark.integration
async def test_all_three_layers_raise_yields_requires_review_with_errors(db, monkeypatch) -> None:
    _patch_layers(
        monkeypatch,
        halal=RuntimeError("halal terminal 500"),
        inv=RuntimeError("sec edgar timeout"),
        val=RuntimeError("yfinance ip-blocked"),
    )
    out = await ss.compute_synthesis("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    assert out.overall_verdict == "REQUIRES_REVIEW"
    assert out.halal.available is False
    assert out.investissable.available is False
    assert out.valuation.available is False
    assert len(out.errors) == 3


# ─── Parallel execution timing ──────────────────────────────────────────────


@pytest.mark.integration
async def test_three_layers_run_in_parallel_not_sequentially(db, monkeypatch) -> None:
    """Each layer sleeps 0.4s; total wall-clock should be ≈ 0.4s (parallel),
    NOT 1.2s (sequential)."""
    DELAY = 0.4

    async def slow(report):
        async def _co(*_a, **_kw):
            await asyncio.sleep(DELAY)
            return report
        return _co

    monkeypatch.setattr(
        ss.shariah_service,
        "screen_with_personal_thresholds",
        await slow(_halal("PASS")),
    )
    monkeypatch.setattr(
        ss.investissable_service,
        "compute_investissable",
        await slow(_inv("OUI")),
    )
    monkeypatch.setattr(
        ss.valuation_service,
        "compute_valuation",
        await slow(_val("OUI")),
    )

    t0 = time.perf_counter()
    out = await ss.compute_synthesis("AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=_factory_yielding(db))
    elapsed = time.perf_counter() - t0

    assert out.overall_verdict == "INVESTABLE"
    # Wide tolerance for CI jitter, but well below 3 × DELAY = 1.2s.
    assert elapsed < DELAY * 2.5, (
        f"layers ran sequentially: elapsed {elapsed:.2f}s, expected < {DELAY * 2.5:.2f}s"
    )


# ─── Regression — Step 7.1 hotfix ───────────────────────────────────────────


@pytest.mark.integration
async def test_each_branch_gets_an_isolated_session(db, monkeypatch) -> None:
    """Regression test for the Step-6 bug: when the 3 layers share a single
    ``AsyncSession``, asyncpg raises ``InterfaceError: another operation in
    progress`` on the slowest branch (valuation), surfacing as
    ``valuation.available = False`` / ``verdict = "ERROR"`` in the bandeau
    while the underlying valuation endpoint works perfectly.

    Fix: each branch acquires its own session from ``session_factory``.
    This test instruments each layer to read its session identity (via
    ``id(session)``) — the 3 ids must be distinct.
    """
    seen_ids: list[int] = []

    async def halal_co(_sym, session, *_a, **_kw):
        seen_ids.append(id(session))
        await asyncio.sleep(0.05)
        return _halal("PASS")

    async def inv_co(_sym, session, *_a, **_kw):
        seen_ids.append(id(session))
        await asyncio.sleep(0.05)
        return _inv("OUI")

    async def val_co(_sym, session, *_a, **_kw):
        seen_ids.append(id(session))
        await asyncio.sleep(0.05)
        return _val("NON", "SURÉVALUÉE")

    monkeypatch.setattr(ss.shariah_service, "screen_with_personal_thresholds", halal_co)
    monkeypatch.setattr(ss.investissable_service, "compute_investissable", inv_co)
    monkeypatch.setattr(ss.valuation_service, "compute_valuation", val_co)

    # Factory returns a FRESH context manager every call (a new "session"
    # would be a new asyncpg connection in production). Yield distinct
    # sentinel objects so the 3 ids must differ.
    counter = {"n": 0}

    @asynccontextmanager
    async def factory():
        counter["n"] += 1
        # Use a tagged proxy object so id() differs across calls.
        proxy = type("S", (), {"tag": counter["n"], "db": db})()
        yield proxy

    out = await ss.compute_synthesis(
        "AAPL", AsyncMock(), AsyncMock(), AsyncMock(), session_factory=factory,
    )

    assert len(seen_ids) == 3
    assert len(set(seen_ids)) == 3, f"layers shared a session: {seen_ids}"
    # And the bandeau still maps the real verdict — not "ERROR".
    assert out.valuation.available is True
    assert out.valuation.verdict == "NON"
    assert out.valuation.label == "SURÉVALUÉE"
    assert out.overall_verdict == "REQUIRES_REVIEW"
