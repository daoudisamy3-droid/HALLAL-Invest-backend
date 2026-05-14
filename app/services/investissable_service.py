"""INVESTISSABLE orchestrator — Step 4.

Spec authority: §4.2 (gates + quality), §4.2.3 (final verdict), §4.2.4
(API output shape). The orchestrator runs three short-circuiting gates
followed by a weighted quality score over up to five components.

V1 deviations vs spec (cf. plan + ``docs/INVESTISSABLE_NON_US_LIMITATION.md``
and ``docs/SCORE_PARTIAL_COMPONENTS.md``):

  - Altman uses Z'' uniformly (no live MC source in V1).
  - Smart Money + Earnings Stability are not computable (no
    YFinance/Finnhub) → both components return None and the weighted
    aggregator redistributes the 30 % of total weight over the 3
    remaining components (Piotroski 30 % + Growth 25 % + Capital 15 %
    → renormalised on 70 % effective weight).
  - Sector medians are unavailable → Fraud-S1 and Capital-D3 use the
    absolute fallback thresholds the spec already documents.
  - Non-US tickers (not in SEC EDGAR's universe): the financials layer
    returns no data, so the score components can't run. The orchestrator
    returns ``verdict=INCERTAIN`` with the AAOIFI gate result preserved
    (so the user still sees the halal verdict) and a clear warning.

All arithmetic stays in Decimal in the underlying components; this
orchestrator exposes float scores at the schema boundary (consistent
with the spec's JSON sample).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import HalalTerminalError, SECEdgarError
from app.integration.halal_terminal_client import HalalTerminalClient
from app.integration.sec_edgar_client import SecEdgarClient
from app.schemas.investissable import (
    DataCompleteness,
    GateResult,
    InvestissableReport,
    InvestissableVerdict,
    QualityComponent,
)
from app.services import (
    altman,
    capital_allocation,
    financials_service,
    fraud,
    growth,
    piotroski,
    shariah_service,
)
from app.services.sector import SectorInfo, classify_from_submissions

logger = logging.getLogger(__name__)


# Spec §4.2.2 weighted aggregator
_QUALITY_WEIGHTS: dict[str, float] = {
    "piotroski":          0.30,
    "growth":             0.25,
    "smart_money":        0.20,
    "capital_allocation": 0.15,
    "earnings_stability": 0.10,
}

# Verdict label boundaries (spec §4.2.3)
_LABEL_EXCELLENT = 75
_LABEL_GOOD = 60
_LABEL_AVERAGE = 45


async def compute_investissable(
    symbol: str,
    db: AsyncSession,
    halal_client: HalalTerminalClient,
    sec_client: SecEdgarClient,
) -> InvestissableReport:
    """End-to-end pipeline for the INVESTISSABLE onglet."""
    sym = symbol.strip().upper()
    warnings: list[str] = []

    # ── Gate 1 — AAOIFI (etape 1, reused) ────────────────────────────────
    shariah_report = await shariah_service.screen_with_personal_thresholds(
        sym, db, halal_client
    )
    aaoifi_gate = _aaoifi_gate_from_shariah(shariah_report)

    if aaoifi_gate.verdict == "FAIL":
        return _shortcircuit_verdict(
            symbol=sym,
            verdict="NON",
            blocked_at="aaoifi",
            reason=shariah_report.reason or "AAOIFI non conforme",
            gates={"aaoifi": aaoifi_gate},
            data_completeness="INSUFFICIENT",
            warnings=warnings,
        )

    if aaoifi_gate.verdict == "INSUFFICIENT_DATA":
        # AAOIFI ERROR / incomplete — gate blocks per master-prompt §5.3.
        return _shortcircuit_verdict(
            symbol=sym,
            verdict="INCERTAIN",
            blocked_at="aaoifi",
            reason=shariah_report.reason or "AAOIFI verdict indisponible",
            gates={"aaoifi": aaoifi_gate},
            data_completeness="INSUFFICIENT",
            warnings=warnings,
        )

    # ── SEC financials needed for all subsequent gates + quality ─────────
    facts_tuple: tuple[int, str, dict[str, Any]] | None
    try:
        facts_tuple = await financials_service.get_facts_payload(sym, db, sec_client)
    except SECEdgarError as exc:
        logger.warning("investissable SEC error symbol=%s err=%s", sym, exc)
        warnings.append(f"SEC EDGAR indisponible: {exc}. Score qualité non calculé.")
        return InvestissableReport(
            symbol=sym,
            verdict="INCERTAIN",
            reason="SEC EDGAR transient failure — see warnings.",
            gates={"aaoifi": aaoifi_gate},
            quality_components={},
            data_completeness="INSUFFICIENT",
            warnings=warnings,
        )

    if facts_tuple is None:
        # Non-US ticker — documented V1 limitation (Q9 plan).
        warnings.append(
            "Ticker non couvert par SEC EDGAR (probablement non-US). "
            "Verdict INVESTISSABLE limité au gate AAOIFI en V1 — cf. "
            "docs/INVESTISSABLE_NON_US_LIMITATION.md."
        )
        return InvestissableReport(
            symbol=sym,
            verdict="INCERTAIN",
            reason=(
                "Ticker non-US : aucun calcul SCORE possible en V1 (SEC EDGAR US-only). "
                "Seul le gate AAOIFI est exploitable."
            ),
            gates={"aaoifi": aaoifi_gate},
            quality_components={},
            data_completeness="INSUFFICIENT",
            warnings=warnings,
        )

    cik, _entity_name, facts = facts_tuple

    # Submissions (sector + restatements). Tolerant: fraud-Signal-3 surfaces
    # `null` if this fails.
    submissions: dict[str, Any] | None
    try:
        submissions = await sec_client.get_submissions(cik)
    except SECEdgarError as exc:
        logger.info("investissable submissions miss symbol=%s err=%s", sym, exc)
        submissions = None
        warnings.append(f"SEC submissions indisponible — signal restatements skip: {exc}")

    sector_info = classify_from_submissions(submissions)

    # ── Gate 2 — Altman Z'' ──────────────────────────────────────────────
    altman_result = altman.compute(facts)
    altman_gate = _to_gate_result(
        verdict=altman_result.verdict,
        details={
            "z_score": str(altman_result.z_score) if altman_result.z_score is not None else None,
            "zone": altman_result.zone,
            "inputs": {k: str(v) if v is not None else None for k, v in altman_result.inputs.items()},
            "formula": "Z''",
            "calculation_detail": altman_result.calculation_detail,
        },
        reason=altman_result.reason,
    )

    if altman_gate.verdict == "FAIL":
        return _shortcircuit_verdict(
            symbol=sym,
            verdict="NON",
            blocked_at="altman",
            reason=altman_result.reason or "Risque de faillite (Altman Z-Score)",
            gates={"aaoifi": aaoifi_gate, "altman": altman_gate},
            data_completeness="PARTIAL",
            warnings=warnings,
        )

    if altman_gate.verdict == "INSUFFICIENT_DATA":
        return _shortcircuit_verdict(
            symbol=sym,
            verdict="INCERTAIN",
            blocked_at="altman",
            reason=altman_result.reason or "Altman non calculable (données SEC partielles)",
            gates={"aaoifi": aaoifi_gate, "altman": altman_gate},
            data_completeness="PARTIAL",
            warnings=warnings,
        )

    if altman_gate.verdict == "WARNING":
        warnings.append(
            f"Altman Z'' = {altman_result.z_score} en zone grise [1.1, 2.6) — bandeau orange à afficher."
        )

    # ── Gate 3 — Fraud ───────────────────────────────────────────────────
    fraud_result = fraud.compute(facts, sector_info, submissions)
    fraud_gate = _to_gate_result(
        verdict=fraud_result.verdict,
        details={
            "positive_signals_count": fraud_result.positive_signals_count,
            "signals": {
                name: {
                    "positive": s.positive,
                    **{k: v for k, v in s.details.items()},
                    "calculation_detail": s.calculation_detail,
                }
                for name, s in fraud_result.signals.items()
            },
        },
        reason=fraud_result.reason,
    )

    if fraud_gate.verdict == "FAIL":
        return _shortcircuit_verdict(
            symbol=sym,
            verdict="NON",
            blocked_at="fraud",
            reason=fraud_result.reason or "Signaux de fraude détectés",
            gates={"aaoifi": aaoifi_gate, "altman": altman_gate, "fraud": fraud_gate},
            data_completeness="PARTIAL",
            warnings=warnings,
        )

    # ── Quality score — all gates passed ─────────────────────────────────
    components, score, completeness = _compute_quality(facts, sector_info)
    if components.get("smart_money") and not components["smart_money"].available:
        warnings.append(
            "Smart Money non calculable en V1 (pas d'intégration YFinance/Finnhub) — "
            "poids redistribué automatiquement, cf. docs/SCORE_PARTIAL_COMPONENTS.md."
        )
    if components.get("earnings_stability") and not components["earnings_stability"].available:
        warnings.append(
            "Earnings Stability non calculable en V1 (pas d'intégration Finnhub estimates) — "
            "poids redistribué automatiquement, cf. docs/SCORE_PARTIAL_COMPONENTS.md."
        )

    verdict, label = _final_verdict(score)

    return InvestissableReport(
        symbol=sym,
        verdict=verdict,
        label=label,
        quality_score=score,
        gates={"aaoifi": aaoifi_gate, "altman": altman_gate, "fraud": fraud_gate},
        quality_components=components,
        data_completeness=completeness,
        warnings=warnings,
        reason=None,
    )


# ─── Quality aggregation ────────────────────────────────────────────────────


def _compute_quality(
    facts: dict[str, Any],
    sector_info: SectorInfo,
) -> tuple[dict[str, QualityComponent], float | None, DataCompleteness]:
    """Run components + weighted aggregator. Returns (components, score, completeness)."""
    piotroski_res = piotroski.compute(facts)
    growth_res = growth.compute(facts)
    capital_res = capital_allocation.compute(facts, sector_info)

    components: dict[str, QualityComponent] = {
        "piotroski": QualityComponent(
            score=piotroski_res.score,
            available=piotroski_res.available,
            details={
                "raw": f"{piotroski_res.raw}/{piotroski_res.n_evaluated}"
                       if piotroski_res.raw is not None else None,
                "n_evaluated": piotroski_res.n_evaluated,
                "criteria": piotroski_res.criteria,
                "calculation_detail": piotroski_res.calculation_detail,
            },
        ),
        "growth": QualityComponent(
            score=growth_res.score,
            available=growth_res.available,
            details={
                "cagrs_3y": {k: str(v) if v is not None else None
                             for k, v in growth_res.raw_cagrs.items()},
                "sub_scores": growth_res.sub_scores,
                "calculation_detail": growth_res.calculation_detail,
            },
        ),
        "smart_money": QualityComponent(
            score=None,
            available=False,
            details={
                "reason": "Not computable in V1 — no YFinance/Finnhub integration. "
                          "See docs/SCORE_PARTIAL_COMPONENTS.md.",
            },
        ),
        "capital_allocation": QualityComponent(
            score=capital_res.score,
            available=capital_res.available,
            details={
                "d1_buybacks": capital_res.d1_buybacks,
                "d3_investment_intensity": capital_res.d3_investment_intensity,
                **capital_res.details,
            },
        ),
        "earnings_stability": QualityComponent(
            score=None,
            available=False,
            details={
                "reason": "Not computable in V1 — no Finnhub estimates source. "
                          "See docs/SCORE_PARTIAL_COMPONENTS.md.",
            },
        ),
    }

    # Weighted aggregation with renormalisation per §4.2.2.
    available_pairs = [
        (name, comp.score)
        for name, comp in components.items()
        if comp.available and comp.score is not None
    ]
    n_available = len(available_pairs)
    if n_available == 0:
        return components, None, "INSUFFICIENT"

    total_weight = sum(_QUALITY_WEIGHTS[name] for name, _ in available_pairs)
    weighted_sum = sum(_QUALITY_WEIGHTS[name] * score for name, score in available_pairs)
    score = round(weighted_sum / total_weight, 1)

    # Per spec §4.2.2: min 3 composantes for a credible score; below that
    # we still expose the score but flag completeness as INSUFFICIENT.
    completeness: DataCompleteness
    if n_available == 5:
        completeness = "FULL"
    elif n_available >= 3:
        completeness = "PARTIAL"
    else:
        completeness = "INSUFFICIENT"

    return components, score, completeness


def _final_verdict(score: float | None) -> tuple[InvestissableVerdict, str | None]:
    if score is None:
        return "INCERTAIN", None
    if score >= _LABEL_EXCELLENT:
        return "OUI", "OUI - QUALITÉ EXCELLENTE"
    if score >= _LABEL_GOOD:
        return "OUI", "OUI - QUALITÉ BONNE"
    if score >= _LABEL_AVERAGE:
        return "OUI", "OUI - QUALITÉ MOYENNE"
    return "NON", "NON - QUALITÉ INSUFFISANTE"


# ─── Mappers ─────────────────────────────────────────────────────────────────


def _aaoifi_gate_from_shariah(report: Any) -> GateResult:
    """Map a ShariahReport (étape 1) to our generic GateResult shape."""
    sv = report.verdict  # "PASS" | "FAIL" | "ERROR" | "NOT_COVERED"
    if sv == "PASS":
        gate_verdict = "PASS"
    elif sv == "FAIL":
        gate_verdict = "FAIL"
    elif sv in ("ERROR", "NOT_COVERED"):
        # Both block any downstream decision per master-prompt §5.3.
        gate_verdict = "INSUFFICIENT_DATA"
    else:  # defensive — should never happen
        gate_verdict = "INSUFFICIENT_DATA"

    return GateResult(
        verdict=gate_verdict,
        details={
            "shariah_verdict": sv,
            "source": report.source,
            "cached": report.cached,
            "cache_age_days": report.cache_age_days,
            "halal_terminal_methodology_verdicts": {
                k: v.model_dump() for k, v in report.halal_terminal_methodology_verdicts.items()
            } if report.halal_terminal_methodology_verdicts else {},
            "failed_checks": list(report.failed_checks),
        },
        reason=report.reason,
    )


def _to_gate_result(
    verdict: str, details: dict[str, Any], reason: str | None
) -> GateResult:
    return GateResult(verdict=verdict, details=details, reason=reason)


def _shortcircuit_verdict(
    *,
    symbol: str,
    verdict: InvestissableVerdict,
    blocked_at: str,
    reason: str,
    gates: dict[str, GateResult],
    data_completeness: DataCompleteness,
    warnings: list[str],
) -> InvestissableReport:
    return InvestissableReport(
        symbol=symbol,
        verdict=verdict,
        blocked_at=blocked_at,  # type: ignore[arg-type]
        reason=reason,
        gates=gates,  # type: ignore[arg-type]
        quality_components={},
        data_completeness=data_completeness,
        warnings=warnings,
    )
