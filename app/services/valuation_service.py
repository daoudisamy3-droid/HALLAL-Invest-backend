"""Valuation service — Step 4.5 + Step 5 (§4.3 — onglet "Bien valorisée ?").

Spec authority: §4.3.1-§4.3.6 — 4 methods + median aggregation + verdict.

V1 implementation status (Step 5 reactivates M1 and M4 via YFinance):

  ✅ Method 1 — Multiples vs 5y history   (P/E only; P/S + EV/EBITDA deferred)
  ❌ Method 2 — Multiples vs sector       (needs FMP peer groups + sector medians)
  ✅ Method 3 — Graham Number             (computable from SEC EDGAR alone)
  ✅ Method 4 — Analyst target consensus  (YFinance ``targetMedianPrice``)

When the caller doesn't pass a YFinance client (legacy code path), or
when YFinance silently fails, M1 and M4 fall back to ``available=False``
with the same explicit reasons as Step 4.5.

See ``docs/VALUATION_PARTIAL_METHODS.md`` and
``docs/YFINANCE_INTEGRATION_NOTES.md`` for the full narrative.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import SECEdgarError
from app.integration.sec_edgar_client import SecEdgarClient
from app.integration.yfinance_client import YFinanceClient
from app.schemas.valuation import (
    MethodResult,
    ValuationConfidence,
    ValuationMethodName,
    ValuationReport,
    ValuationVerdict,
)
from app.services import financials_service, yfinance_service

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
    *,
    yfinance_client: YFinanceClient | None = None,
) -> ValuationReport:
    """End-to-end pipeline for the BIEN-VALORISÉE onglet (§4.3).

    ``yfinance_client``: when provided, unlocks Method 1 (multiples 5y
    historical, P/E flavour) and Method 4 (analyst target). On any
    YFinance failure those methods individually fall back to
    ``available=False`` — Graham always stays computable from SEC alone.
    """
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

    # Fetch live data from YFinance up-front (once each, then re-used by
    # M1, M4, and the verdict aggregator). On any failure the helper
    # returns None and the dependent method gracefully fails too.
    info: dict[str, Any] | None = None
    history: list[dict[str, Any]] | None = None
    current_price: Decimal | None = None
    if yfinance_client is not None:
        info = await yfinance_service.get_info(sym, db, yfinance_client)
        history = await yfinance_service.get_history(
            sym, db, yfinance_client, period="5y", interval="1mo"
        )
        current_price = _extract_current_price(info)
        if current_price is None:
            warnings.append(
                "YFinance: current price indisponible — verdict global "
                "INDÉTERMINÉ même si méthodes calculables."
            )
    else:
        warnings.append(
            "YFinance client non câblé sur cette route — méthodes 1 et 4 "
            "indisponibles. Cf. docs/VALUATION_PARTIAL_METHODS.md."
        )

    # Run the 4 methods.
    methods: dict[ValuationMethodName, MethodResult] = {
        "vs_historical_5y": _method_vs_historical_5y(facts, history, current_price),
        "vs_sector":        _method_vs_sector(facts),
        "graham_number":    _method_graham_number(facts),
        "analyst_target":   _method_analyst_target(info),
    }

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
            "calculation_detail": {
                "formula": "Graham Number = sqrt(22.5 × EPS × BVPS)",
                "variables": {
                    "EPS (diluted, latest FY)":           str(eps),
                    "Equity (StockholdersEquity)":         str(equity),
                    "Shares (CommonStockSharesOutstanding)": str(shares),
                    "BVPS = Equity / Shares":              str(bvps),
                },
                "intermediates": {
                    "22.5 × EPS × BVPS": str(inside),
                },
                "computation_steps": [
                    f"BVPS = {equity} / {shares} = {bvps}",
                    f"22.5 × {eps} × {bvps} = {inside}",
                    f"Graham = sqrt({inside}) = {graham}",
                ],
                "result": str(graham),
                "thresholds": [
                    {"label": "fair_value", "condition": "= Graham number (conservatif, growth stocks systématiquement bas)"},
                ],
                "interpretation": (
                    f"Graham fair value = {graham} (conservatif — référence pour value stocks, "
                    "sous-évalue les growth stocks)."
                ),
            },
            "note": (
                "Graham Number is conservative; for growth stocks (AAPL, MSFT…) "
                "it will systematically sit below market price — that's expected."
            ),
        },
        reason=None,
    )


# ─── Methods 1, 2, 4 — V1 scaffolds (always unavailable) ────────────────────


def _method_vs_historical_5y(
    facts: dict[str, Any],
    history: list[dict[str, Any]] | None,
    current_price: Decimal | None,
) -> MethodResult:
    """Multiples vs 5y historical median (§4.3.1).

    V1 implementation (Step 5): P/E only. P/S and EV/EBITDA need
    historical shares-outstanding (and EBITDA) by quarter — deferred to
    a future step that adds shares-history extraction. Documented in
    the response ``details``.

    Algorithm:
      1. From SEC: last 5 (up to 6) FY EPS values via extract_n_year_annuals.
      2. From YFinance: 5y monthly closing prices.
      3. For each bar, map to the EPS of the most recent FY whose
         period_end is ≤ bar date.
      4. P/E_observed = close / mapped EPS. Skip rows where EPS ≤ 0.
      5. Historical median P/E = median of those observations.
      6. Fair value = historical_pe_median × latest_eps.

    Fails gracefully (``available=False`` + reason) when:
      - YFinance history is missing,
      - SEC EPS history is missing or only the latest year is present,
      - the latest EPS is non-positive (P/E undefined),
      - fewer than 12 valid P/E observations could be mapped.
    """
    if history is None or not history:
        return MethodResult(
            available=False,
            fair_value=None,
            details={},
            reason=(
                "Méthode 1: YFinance n'a pas retourné l'historique 5 ans. "
                "Fallback non calculable."
            ),
        )

    eps_history = financials_service.extract_n_year_annuals(facts, "eps_diluted", n=6)
    if len(eps_history) < 2:
        return MethodResult(
            available=False,
            fair_value=None,
            details={"eps_years_available": len(eps_history)},
            reason=(
                "Méthode 1: moins de 2 ans d'EPS historique SEC — médian P/E "
                "non significatif."
            ),
        )

    latest_eps_date, latest_eps = eps_history[0]
    if latest_eps <= Decimal("0"):
        return MethodResult(
            available=False,
            fair_value=None,
            details={"latest_eps": str(latest_eps)},
            reason=(
                f"Méthode 1: EPS latest non-positif ({latest_eps}) — P/E "
                "indéfini."
            ),
        )

    pe_values: list[Decimal] = []
    for bar in history:
        try:
            bar_date = date.fromisoformat(str(bar.get("date", "")))
            close = Decimal(str(bar.get("close", "")))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if close <= Decimal("0"):
            continue
        eps_for_bar = _eps_for_date(eps_history, bar_date)
        if eps_for_bar is None or eps_for_bar <= Decimal("0"):
            continue
        pe_values.append(close / eps_for_bar)

    if len(pe_values) < 12:
        return MethodResult(
            available=False,
            fair_value=None,
            details={
                "n_pe_observations": len(pe_values),
                "n_bars": len(history),
                "n_eps_years": len(eps_history),
            },
            reason=(
                f"Méthode 1: seulement {len(pe_values)} observations P/E "
                "exploitables (<12 mois) — médiane non robuste."
            ),
        )

    pe_median = _median(pe_values)
    fair_value = pe_median * latest_eps

    return MethodResult(
        available=True,
        fair_value=fair_value,
        details={
            "current_price": str(current_price) if current_price is not None else None,
            "latest_eps": str(latest_eps),
            "latest_eps_period_end": latest_eps_date.isoformat(),
            "historical_pe_median": str(pe_median),
            "n_pe_observations": len(pe_values),
            "calculation_detail": {
                "formula": (
                    "M1 fair value = historical_P/E_median × latest_EPS"
                    "  (P/E_t = close_t / EPS_FY(t))"
                ),
                "variables": {
                    "latest EPS":                str(latest_eps),
                    "latest EPS period_end":     latest_eps_date.isoformat(),
                    "n bars 5y monthly":          str(len(history)),
                    "n EPS years":                str(len(eps_history)),
                    "current price (YFinance)":   str(current_price) if current_price else None,
                },
                "intermediates": {
                    "P/E observations exploitables": str(len(pe_values)),
                    "historical P/E median":         str(pe_median),
                },
                "computation_steps": [
                    f"Construire la série P/E_t = close_t / EPS_FY(t) sur {len(history)} barres mensuelles 5y",
                    f"Filtrer EPS > 0 → {len(pe_values)} observations exploitables",
                    f"median(P/E) = {pe_median}",
                    f"fair value = {pe_median} × {latest_eps} = {fair_value}",
                ],
                "result": str(fair_value),
                "thresholds": [
                    {"label": "fair_value", "condition": "= médiane P/E historique × EPS courant"},
                ],
                "interpretation": (
                    f"Si current_price ≈ {fair_value}, le multiple P/E est à sa "
                    "médiane 5y. Au-dessus = surévalué relativement à son propre passé."
                ),
            },
            "method_v1": (
                "P/E only — P/S et EV/EBITDA reportés (shares-history et "
                "EBITDA-history non encore intégrés)."
            ),
        },
        reason=None,
    )


def _eps_for_date(
    eps_history: list[tuple[date, Decimal]],
    target: date,
) -> Decimal | None:
    """EPS of the most recent FY whose period_end is on or before ``target``.

    ``eps_history`` is sorted most-recent-first (per
    ``financials_service.extract_n_year_annuals`` contract).
    """
    for period_end, eps in eps_history:
        if period_end <= target:
            return eps
    return None


def _median(values: list[Decimal]) -> Decimal:
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / Decimal("2")


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


def _method_analyst_target(info: dict[str, Any] | None) -> MethodResult:
    """Analyst target median (§4.3.4).

    Step 5 implementation: ``yfinance.Ticker(symbol).info["targetMedianPrice"]``
    with reliability guard ``numberOfAnalystOpinions >= 5`` (spec §4.3.4).

    Fails gracefully when:
      - YFinance unavailable (info is None),
      - targetMedianPrice missing or non-positive,
      - fewer than 5 analyst opinions.
    """
    if not isinstance(info, dict):
        return MethodResult(
            available=False,
            fair_value=None,
            details={},
            reason=(
                "Méthode 4: YFinance .info indisponible — target médian analystes "
                "non récupérable."
            ),
        )

    raw_target = info.get("targetMedianPrice")
    n_analysts_raw = info.get("numberOfAnalystOpinions")
    target: Decimal | None = None
    if raw_target is not None:
        try:
            target = Decimal(str(raw_target))
        except (InvalidOperation, ValueError, TypeError):
            target = None

    n_analysts: int | None = None
    if n_analysts_raw is not None:
        try:
            n_analysts = int(n_analysts_raw)
        except (TypeError, ValueError):
            n_analysts = None

    if target is None or target <= Decimal("0"):
        return MethodResult(
            available=False,
            fair_value=None,
            details={
                "target_median_price": str(raw_target) if raw_target is not None else None,
                "number_of_analyst_opinions": n_analysts,
            },
            reason=(
                "Méthode 4: targetMedianPrice absent ou non-positif dans YFinance "
                ".info — consensus analystes indisponible."
            ),
        )

    if n_analysts is None or n_analysts < 5:
        return MethodResult(
            available=False,
            fair_value=None,
            details={
                "target_median_price": str(target),
                "number_of_analyst_opinions": n_analysts,
            },
            reason=(
                f"Méthode 4: seulement {n_analysts} analystes (spec §4.3.4 "
                "exige ≥ 5) — consensus jugé non fiable."
            ),
        )

    target_low = info.get("targetLowPrice")
    target_high = info.get("targetHighPrice")

    return MethodResult(
        available=True,
        fair_value=target,
        details={
            "target_median_price": str(target),
            "target_low_price": str(target_low) if target_low is not None else None,
            "target_high_price": str(target_high) if target_high is not None else None,
            "number_of_analyst_opinions": n_analysts,
            "calculation_detail": {
                "formula": "M4 fair value = targetMedianPrice (consensus analystes Wall Street)",
                "variables": {
                    "targetMedianPrice (YFinance)":     str(target),
                    "targetLowPrice":                    str(target_low) if target_low is not None else None,
                    "targetHighPrice":                   str(target_high) if target_high is not None else None,
                    "numberOfAnalystOpinions":           str(n_analysts),
                },
                "intermediates": {},
                "computation_steps": [
                    f"Consensus analystes ({n_analysts} avis) — médiane retenue : {target}",
                ],
                "result": str(target),
                "thresholds": [
                    {"label": "fiable",      "condition": "numberOfAnalystOpinions ≥ 5"},
                    {"label": "non fiable",  "condition": "numberOfAnalystOpinions < 5 → méthode reportée"},
                ],
                "interpretation": (
                    f"{n_analysts} analystes ≥ 5 (seuil spec §4.3.4) → "
                    f"target médian retenu comme fair value = {target}."
                ),
            },
        },
        reason=None,
    )


def _extract_current_price(info: dict[str, Any] | None) -> Decimal | None:
    """Best-effort current price from YFinance .info (priority-ordered keys)."""
    if not isinstance(info, dict):
        return None
    for key in ("regularMarketPrice", "currentPrice", "previousClose"):
        raw = info.get(key)
        if raw is None:
            continue
        try:
            value = Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError):
            continue
        if value > Decimal("0"):
            return value
    return None


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
