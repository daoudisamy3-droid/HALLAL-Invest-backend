"""Valuation service — Step 4.5 (§4.3 — onglet "Bien valorisée ?").

Spec authority: §4.3.1-§4.3.6 — 4 methods + median aggregation + verdict.

V1 implementation status (Q1 plan validated — Option A "skeleton +
Graham only"):

  ✅ Method 3 — Graham Number     (computable from SEC EDGAR alone)
  ❌ Method 1 — Multiples vs 5y history    (needs live price + historical price/multiples)
  ❌ Method 2 — Multiples vs sector        (needs FMP peer groups + sector medians)
  ❌ Method 4 — Analyst target consensus   (needs YFinance or Finnhub estimates)

The 3 unavailable methods return ``MethodResult(available=False, reason=...)``
with human-readable explanations. Their plug-in points are documented
in-code so a future Step 4.6 / 5.x integration only fills the function
body without touching the contract.

See ``docs/VALUATION_PARTIAL_METHODS.md`` for the full limitations
narrative and reintegration plan.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import SECEdgarError
from app.integration.sec_edgar_client import SecEdgarClient
from app.schemas.valuation import (
    MethodResult,
    ValuationConfidence,
    ValuationMethodName,
    ValuationReport,
    ValuationVerdict,
)
from app.services import financials_service

logger = logging.getLogger(__name__)


# Verdict label thresholds (spec §4.3.6) — applied on price/FV ratio.
_RATIO_DEEP_DISCOUNT = Decimal("0.85")
_RATIO_LIGHT_DISCOUNT = Decimal("0.95")
_RATIO_FAIR_HIGH = Decimal("1.05")
_RATIO_HEAVY_OVERVAL = Decimal("1.5")


# ─── Public API ──────────────────────────────────────────────────────────────


async def compute_valuation(
    symbol: str,
    db: AsyncSession,
    sec_client: SecEdgarClient,
) -> ValuationReport:
    """End-to-end pipeline for the BIEN-VALORISÉE onglet (§4.3)."""
    sym = symbol.strip().upper()
    warnings: list[str] = []

    # Fetch facts (or detect non-US ticker / upstream failure).
    facts_tuple: tuple[int, str, dict[str, Any]] | None
    try:
        facts_tuple = await financials_service.get_facts_payload(sym, db, sec_client)
    except SECEdgarError as exc:
        logger.warning("valuation SEC error symbol=%s err=%s", sym, exc)
        return _indetermine_response(
            symbol=sym,
            reason=f"SEC EDGAR indisponible: {exc}",
            warnings=[f"SEC EDGAR transient failure: {exc}"],
            methods=_all_methods_unavailable("SEC EDGAR unavailable"),
        )

    if facts_tuple is None:
        # Non-US ticker — V1 limitation mirrored from step 4.
        warnings.append(
            "Ticker non couvert par SEC EDGAR (probablement non-US). "
            "Valuation Graham non calculable en V1 — cf. "
            "docs/INVESTISSABLE_NON_US_LIMITATION.md."
        )
        return _indetermine_response(
            symbol=sym,
            reason=(
                "Ticker non-US : aucune méthode de valuation calculable en V1 "
                "(SEC EDGAR US-only)."
            ),
            warnings=warnings,
            methods=_all_methods_unavailable("Non-US ticker — SEC EDGAR coverage missing"),
        )

    _cik, _entity_name, facts = facts_tuple

    # Run the 4 methods.
    methods: dict[ValuationMethodName, MethodResult] = {
        "vs_historical_5y": _method_vs_historical_5y(facts),
        "vs_sector":        _method_vs_sector(facts),
        "graham_number":    _method_graham_number(facts),
        "analyst_target":   _method_analyst_target(facts),
    }

    # Aggregate + verdict (no current_price in V1 → ratio undefined → INDÉTERMINÉ).
    current_price: Decimal | None = None  # V1: no live price source
    agg = _aggregate(methods, current_price)

    # Compose user-facing warning if Graham is the only method.
    n_methods = agg["n_methods"]
    if n_methods < 2:
        warnings.append(
            f"Seulement {n_methods} méthode(s) de valuation disponible(s). "
            "Verdict global INDÉTERMINÉ. Cf. docs/VALUATION_PARTIAL_METHODS.md."
        )

    return ValuationReport(
        symbol=sym,
        verdict=agg["verdict"],
        label=agg["label"],
        confidence=agg["confidence"],
        fair_value_median=agg["fair_value_median"],
        fair_value_mean=agg["fair_value_mean"],
        dispersion=agg["dispersion"],
        ratio_price_to_fair_value=agg["ratio"],
        current_price=current_price,
        n_methods=n_methods,
        methods=methods,
        source="computed",
        reason=agg["reason"],
        warnings=warnings,
    )


# ─── Method 3 — Graham Number (THE one that works in V1) ────────────────────


def _method_graham_number(facts: dict[str, Any]) -> MethodResult:
    """Graham Number = sqrt(22.5 × EPS × BVPS).

    Decimal-exact via :meth:`Decimal.sqrt`. Returns ``available=False``
    when EPS or BVPS is missing / non-positive (per spec §4.3.3).

    BVPS computed from SEC as ``StockholdersEquity / SharesOutstanding``.
    EPS is the latest annual diluted EPS.
    """
    eps = financials_service.extract_latest_annual_value(facts, "eps_diluted")
    equity = financials_service.extract_latest_annual_value(facts, "stockholders_equity")
    shares = financials_service.extract_latest_annual_value(facts, "shares_outstanding")

    bvps: Decimal | None = None
    if equity is not None and shares is not None and shares > 0:
        bvps = equity / shares

    if eps is None or bvps is None:
        missing = []
        if eps is None:
            missing.append("eps_diluted")
        if equity is None:
            missing.append("stockholders_equity")
        if shares is None or (shares is not None and shares <= 0):
            missing.append("shares_outstanding")
        return MethodResult(
            available=False,
            fair_value=None,
            details={"eps": str(eps) if eps else None,
                     "bvps": str(bvps) if bvps else None},
            reason=f"Données manquantes pour Graham Number: {', '.join(missing) or 'BVPS non calculable'}",
        )

    if eps <= 0 or bvps <= 0:
        return MethodResult(
            available=False,
            fair_value=None,
            details={"eps": str(eps), "bvps": str(bvps)},
            reason=f"EPS ({eps}) ou BVPS ({bvps}) négatif/nul (Graham requiert > 0).",
        )

    inside = Decimal("22.5") * eps * bvps
    graham = inside.sqrt()

    return MethodResult(
        available=True,
        fair_value=graham,
        details={
            "eps": str(eps),
            "bvps": str(bvps),
            "formula": "sqrt(22.5 × EPS × BVPS)",
            "note": (
                "Graham Number is conservative; for growth stocks (AAPL, MSFT…) "
                "it will systematically sit below market price — that's expected."
            ),
        },
        reason=None,
    )


# ─── Methods 1, 2, 4 — V1 scaffolds (always unavailable) ────────────────────


def _method_vs_historical_5y(facts: dict[str, Any]) -> MethodResult:  # noqa: ARG001
    """Multiples vs 5y historical median (§4.3.1).

    V1 status: **NOT IMPLEMENTED**. Requires:
      - Current price of the equity (no live price source integrated)
      - 5 years of historical prices to back-compute historical P/E,
        P/S, EV/EBITDA medians
      - For EV/EBITDA: market cap (= price × shares) + cash + debt + EBITDA

    Plug-in point: when YFinance integration lands (likely Step 5
    "Portfolio Analytics partie 2" or a dedicated 4.6), replace this
    function's body with the spec snippet (lines 1069-1101) using a
    YFinance client. The :class:`MethodResult` contract here stays
    unchanged.
    """
    return MethodResult(
        available=False,
        fair_value=None,
        details={},
        reason=(
            "Méthode 1 (multiples vs historique 5 ans) requiert l'intégration "
            "YFinance (prix marché + multiples historiques) — non disponible "
            "en V1. Cf. docs/VALUATION_PARTIAL_METHODS.md."
        ),
    )


def _method_vs_sector(facts: dict[str, Any]) -> MethodResult:  # noqa: ARG001
    """Multiples vs sector median (§4.3.2).

    V1 status: **NOT IMPLEMENTED**. Requires:
      - Peer group (20+ tickers from same GICS sector) — spec suggests
        FMP ``/v3/stock_peers`` as starting point
      - Current multiples for the ticker + each peer
      - Computed sector-median P/E, P/S, EV/EBITDA

    Plug-in point: when FMP integration lands, replace this function's
    body with the spec snippet (lines 1107-1132) using a FMP client.
    """
    return MethodResult(
        available=False,
        fair_value=None,
        details={},
        reason=(
            "Méthode 2 (multiples vs médiane sectorielle) requiert l'intégration "
            "FMP (peer groups + multiples sectoriels) — non disponible en V1. "
            "Cf. docs/VALUATION_PARTIAL_METHODS.md."
        ),
    )


def _method_analyst_target(facts: dict[str, Any]) -> MethodResult:  # noqa: ARG001
    """Analyst target median (§4.3.4).

    V1 status: **NOT IMPLEMENTED**. Requires:
      - Wall Street analyst consensus 12-month target (median, low, high)
      - Number of analysts (spec requires ≥ 5 for reliability)

    Plug-in point: YFinance ``Ticker.info["targetMedianPrice"]`` or
    Finnhub ``/stock/price-target``. Replace this function's body once
    the integration is in place.
    """
    return MethodResult(
        available=False,
        fair_value=None,
        details={},
        reason=(
            "Méthode 4 (target médian analystes) requiert l'intégration YFinance "
            "ou Finnhub (consensus analysts) — non disponible en V1. "
            "Cf. docs/VALUATION_PARTIAL_METHODS.md."
        ),
    )


# ─── Aggregation + verdict ──────────────────────────────────────────────────


def _aggregate(
    methods: dict[ValuationMethodName, MethodResult],
    current_price: Decimal | None,
) -> dict[str, Any]:
    """Combine the 4 method results into the aggregated verdict.

    Aggregation rule (spec §4.3.5 + user exigence Q4):
      - fair_value_median, fair_value_mean over available methods
      - dispersion = (max − min) / median  ← user override of spec's stdev/mean
      - ratio = current_price / fair_value_median  (None if no price)
      - verdict: per §4.3.6 thresholds (FR labels)
      - confidence: HIGH if 4 methods + dispersion<0.15, MEDIUM if 3
        methods (or 4 + 0.15-0.30), LOW if 2 methods or dispersion>0.30,
        N/A if < 2 methods

    Always returns a coherent dict (never raises).
    """
    available_values = [
        m.fair_value
        for m in methods.values()
        if m.available and m.fair_value is not None
    ]
    n_methods = len(available_values)

    fair_value_median: Decimal | None = None
    fair_value_mean: Decimal | None = None
    dispersion: Decimal | None = None
    ratio: Decimal | None = None
    verdict: ValuationVerdict = "INDÉTERMINÉ"
    label: str | None = None
    confidence: ValuationConfidence = _confidence(n_methods, None)
    reason: str | None = None

    if n_methods >= 1:
        # Expose the single-method value too — useful in V1 for Graham alone.
        sorted_vals = sorted(available_values)
        if n_methods % 2 == 1:
            fair_value_median = sorted_vals[n_methods // 2]
        else:
            mid_a = sorted_vals[n_methods // 2 - 1]
            mid_b = sorted_vals[n_methods // 2]
            fair_value_median = (mid_a + mid_b) / Decimal("2")
        fair_value_mean = sum(available_values, Decimal("0")) / Decimal(n_methods)

    if n_methods >= 2 and fair_value_median is not None and fair_value_median > 0:
        # Dispersion: (max − min) / median — per user Q4 override.
        dispersion = (max(available_values) - min(available_values)) / fair_value_median
        confidence = _confidence(n_methods, dispersion)

        if current_price is not None and current_price > 0:
            ratio = current_price / fair_value_median
            verdict, label = _verdict_label(ratio)
        else:
            reason = "current_price indisponible — ratio non calculable, verdict INDÉTERMINÉ."
    elif n_methods < 2:
        confidence = "N/A"
        reason = (
            f"Seules {n_methods} méthode(s) disponible(s) ; "
            "le verdict global exige au moins 2 méthodes (spec §4.3.5)."
        )

    return {
        "n_methods": n_methods,
        "fair_value_median": fair_value_median,
        "fair_value_mean": fair_value_mean,
        "dispersion": dispersion,
        "ratio": ratio,
        "verdict": verdict,
        "label": label,
        "confidence": confidence,
        "reason": reason,
    }


def _verdict_label(ratio: Decimal) -> tuple[ValuationVerdict, str]:
    """Map a price/fair-value ratio onto the §4.3.6 grid (FR strict)."""
    if ratio < _RATIO_DEEP_DISCOUNT:
        return "OUI", "SOUS-ÉVALUÉE"
    if ratio < _RATIO_LIGHT_DISCOUNT:
        return "OUI", "JUSTE PRIX (LÉGÈRE DÉCOTE)"
    if ratio < _RATIO_FAIR_HIGH:
        return "OUI_NEUTRE", "JUSTE PRIX"
    if ratio < _RATIO_HEAVY_OVERVAL:
        return "NON", "SURÉVALUÉE"
    return "NON", "FORTEMENT SURÉVALUÉE"


def _confidence(n: int, dispersion: Decimal | None) -> ValuationConfidence:
    """Confidence ladder per user exigence."""
    if n < 2:
        return "N/A"
    if n == 2:
        return "LOW"
    if n == 3:
        return "MEDIUM"
    # n >= 4
    if dispersion is None:
        return "LOW"
    if dispersion < Decimal("0.15"):
        return "HIGH"
    if dispersion < Decimal("0.30"):
        return "MEDIUM"
    return "LOW"


# ─── Helpers for error / non-US paths ───────────────────────────────────────


def _all_methods_unavailable(reason: str) -> dict[ValuationMethodName, MethodResult]:
    """Build a methods dict where every method is unavailable, sharing one reason."""
    return {
        name: MethodResult(available=False, fair_value=None, details={}, reason=reason)
        for name in ("vs_historical_5y", "vs_sector", "graham_number", "analyst_target")
    }


def _indetermine_response(
    *,
    symbol: str,
    reason: str,
    warnings: list[str],
    methods: dict[ValuationMethodName, MethodResult],
) -> ValuationReport:
    return ValuationReport(
        symbol=symbol,
        verdict="INDÉTERMINÉ",
        label=None,
        confidence="N/A",
        fair_value_median=None,
        fair_value_mean=None,
        dispersion=None,
        ratio_price_to_fair_value=None,
        current_price=None,
        n_methods=0,
        methods=methods,
        source="computed",
        reason=reason,
        warnings=warnings,
    )
