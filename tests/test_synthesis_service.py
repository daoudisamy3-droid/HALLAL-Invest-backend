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
from datetime import date
from unittest.mock import AsyncMock

import pytest

from app.schemas.investissable import InvestissableReport
from app.schemas.shariah import ShariahReport
from app.schemas.valuation import ValuationReport
from app.services import synthesis_service as ss


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
    out = await ss.compute_synthesis("AAPL", db, AsyncMock(), AsyncMock(), AsyncMock())
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
    out = await ss.compute_synthesis("AAPL", db, AsyncMock(), AsyncMock(), AsyncMock())
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
    out = await ss.compute_synthesis("AAPL", db, AsyncMock(), AsyncMock(), AsyncMock())
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
    out = await ss.compute_synthesis("AAPL", db, AsyncMock(), AsyncMock(), AsyncMock())
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
    out = await ss.compute_synthesis("AAPL", db, AsyncMock(), AsyncMock(), AsyncMock())
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
    out = await ss.compute_synthesis("AIXA.DE", db, AsyncMock(), AsyncMock(), AsyncMock())
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
    out = await ss.compute_synthesis("AAPL", db, AsyncMock(), AsyncMock(), AsyncMock())
    assert out.overall_verdict == "REQUIRES_REVIEW"


@pytest.mark.integration
async def test_valuation_indetermine_yields_requires_review(db, monkeypatch) -> None:
    _patch_layers(
        monkeypatch,
        halal=_halal("PASS"),
        inv=_inv("OUI"),
        val=_val("INDÉTERMINÉ", None),
    )
    out = await ss.compute_synthesis("AAPL", db, AsyncMock(), AsyncMock(), AsyncMock())
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
    out = await ss.compute_synthesis("AAPL", db, AsyncMock(), AsyncMock(), AsyncMock())
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
    out = await ss.compute_synthesis("AAPL", db, AsyncMock(), AsyncMock(), AsyncMock())
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
    out = await ss.compute_synthesis("AAPL", db, AsyncMock(), AsyncMock(), AsyncMock())
    elapsed = time.perf_counter() - t0

    assert out.overall_verdict == "INVESTABLE"
    # Wide tolerance for CI jitter, but well below 3 × DELAY = 1.2s.
    assert elapsed < DELAY * 2.5, (
        f"layers ran sequentially: elapsed {elapsed:.2f}s, expected < {DELAY * 2.5:.2f}s"
    )
