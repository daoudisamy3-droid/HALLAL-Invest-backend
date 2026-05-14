"""Fraud-detection gate (Step 4 — bloquant Gate 3).

Spec authority: §4.2.1 Gate 3 — three composite signals; if **2 of 3
positive** the gate FAILs. Each individual signal returning ``None``
(insufficient data) is treated as "not positive" for the gate verdict
but surfaced transparently in the details.

Signals (V1 implementation — Q4 plan: no sector medians, absolute
fallbacks; Q5 plan: get_recent_filings now implemented for Signal 3):

  S1 — FCF/NI quality (3-year average)
       sector_median indisponible → fallback absolu < 0.5,
       avec exemption des secteurs capital-intensive (sector.is_exempt_*).

  S2 — Receivables growth anomaly
       sector_median indisponible → fallback absolu : ratio
       (receivables CAGR 2y / revenue CAGR 2y) > 2.0.

  S3 — Restatements / late-filings frequency
       Count of 10-K/A, 10-Q/A, NT 10-K, NT 10-Q over the past 3 years
       from SEC EDGAR submissions. ≥ 3 → positive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Literal

from app.services import financials_service as fs
from app.services.growth import _cagr
from app.services.sector import SectorInfo


_FCF_NI_3Y_THRESHOLD = Decimal("0.5")
_RECEIVABLES_GROWTH_RATIO_THRESHOLD = Decimal("2.0")
_RESTATEMENTS_THRESHOLD = 3
_RESTATEMENT_FORMS = {"10-K/A", "10-Q/A", "NT 10-K", "NT 10-Q"}
_RESTATEMENTS_LOOKBACK_YEARS = 3


FraudVerdict = Literal["PASS", "FAIL"]


_S1_FORMULA = "FCF/NI = sum(FCF_y0..y-2) / sum(NI_y0..y-2)  with FCF = OCF − CapEx"
_S1_THRESHOLDS = [
    {"label": "clean (signal négatif)", "condition": "ratio ≥ 0.5"},
    {"label": "alerte fraude",          "condition": "ratio < 0.5"},
    {"label": "exempt (capital-intensive)", "condition": "SIC in EXEMPT_SIC_RANGES → toujours clean"},
]

_S2_FORMULA = "Receivables anomaly = CAGR2y(Receivables) / CAGR2y(Revenue)"
_S2_THRESHOLDS = [
    {"label": "clean", "condition": "ratio ≤ 2.0 (créances suivent les revenus)"},
    {"label": "alerte fraude", "condition": "ratio > 2.0 (créances accélèrent vs revenus)"},
]

_S3_FORMULA = "count(form in {10-K/A, 10-Q/A, NT 10-K, NT 10-Q} sur 3 ans glissants)"
_S3_THRESHOLDS = [
    {"label": "clean", "condition": "count < 3"},
    {"label": "alerte fraude", "condition": "count ≥ 3"},
]


@dataclass(frozen=True)
class SignalResult:
    """One sub-signal outcome."""
    positive: bool | None     # True = fraud-positive, False = clean, None = N/A
    details: dict[str, Any]
    calculation_detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class FraudGateResult:
    verdict: FraudVerdict
    positive_signals_count: int
    signals: dict[str, SignalResult]
    reason: str | None


def compute(
    facts: dict[str, Any],
    sector_info: SectorInfo,
    submissions: dict[str, Any] | None,
) -> FraudGateResult:
    """Run the three signals and decide the gate verdict."""
    s1 = _signal_fcf_quality(facts, sector_info)
    s2 = _signal_receivables_anomaly(facts)
    s3 = _signal_restatements(submissions)

    positive_count = sum(1 for s in (s1, s2, s3) if s.positive is True)
    verdict: FraudVerdict = "FAIL" if positive_count >= 2 else "PASS"

    reason: str | None = None
    if verdict == "FAIL":
        triggered = [
            name for name, s in [("fcf_ni", s1), ("receivables", s2), ("restatements", s3)]
            if s.positive is True
        ]
        reason = (
            f"{positive_count}/3 signaux de fraude positifs (≥ 2 = gate FAIL). "
            f"Signaux déclenchés: {', '.join(triggered)}."
        )

    return FraudGateResult(
        verdict=verdict,
        positive_signals_count=positive_count,
        signals={"fcf_ni": s1, "receivables": s2, "restatements": s3},
        reason=reason,
    )


# ─── Signal 1 — FCF/NI quality ──────────────────────────────────────────────


def _signal_fcf_quality(
    facts: dict[str, Any], sector_info: SectorInfo
) -> SignalResult:
    """3-year average FCF/NI ratio; sector-exempt fallback per §4.2.1."""
    ocf_series = fs.extract_n_year_annuals(facts, "operating_cash_flow", n=3)
    capex_series = fs.extract_n_year_annuals(facts, "capex", n=3)
    ni_series = fs.extract_n_year_annuals(facts, "net_income", n=3)

    if len(ocf_series) < 3 or len(ni_series) < 3:
        return SignalResult(
            positive=None,
            details={"reason": "insufficient OCF or NI history (< 3 FY entries)"},
            calculation_detail={
                "formula": _S1_FORMULA,
                "variables": {"n_OCF_years": len(ocf_series), "n_NI_years": len(ni_series)},
                "intermediates": {},
                "computation_steps": ["Historique < 3 ans → signal indéterminé"],
                "result": None,
                "thresholds": _S1_THRESHOLDS,
                "interpretation": "Données insuffisantes pour évaluer S1 (3 ans d'OCF + NI requis).",
            },
        )

    capex_by_end = {d: v for d, v in capex_series}
    fcf_total = sum(
        (ocf - capex_by_end.get(d, Decimal("0")) for d, ocf in ocf_series),
        Decimal("0"),
    )
    ni_total = sum((v for _, v in ni_series), Decimal("0"))

    base_vars = {
        "Σ OCF 3y":   str(sum((v for _, v in ocf_series), Decimal("0"))),
        "Σ CapEx 3y": str(sum((v for _, v in capex_series), Decimal("0"))),
        "Σ FCF 3y":    str(fcf_total),
        "Σ NI 3y":     str(ni_total),
    }

    if ni_total <= 0:
        return SignalResult(
            positive=False,
            details={
                "reason": "3y net income ≤ 0 — FCF/NI ratio undefined; treated as non-positive signal",
                "fcf_3y_total": str(fcf_total),
                "ni_3y_total": str(ni_total),
            },
            calculation_detail={
                "formula": _S1_FORMULA,
                "variables": base_vars,
                "intermediates": {"ratio_3y": "undefined (NI ≤ 0)"},
                "computation_steps": [f"NI total 3y = {ni_total} ≤ 0 → ratio non significatif"],
                "result": None,
                "thresholds": _S1_THRESHOLDS,
                "interpretation": "NI 3y total ≤ 0 → signal traité comme clean par défaut.",
            },
        )

    ratio = fcf_total / ni_total

    if sector_info.is_exempt_from_absolute_fcf_ni:
        return SignalResult(
            positive=False,
            details={
                "ratio_3y": str(ratio),
                "threshold": str(_FCF_NI_3Y_THRESHOLD),
                "exempt_reason": sector_info.exempt_reason,
            },
            calculation_detail={
                "formula": _S1_FORMULA,
                "variables": base_vars,
                "intermediates": {"ratio_3y": str(ratio)},
                "computation_steps": [
                    f"ratio = {fcf_total} / {ni_total} = {ratio}",
                    f"Secteur exempté ({sector_info.exempt_reason}) → signal clean d'office",
                ],
                "result": str(ratio),
                "thresholds": _S1_THRESHOLDS,
                "interpretation": (
                    f"Secteur capital-intensive (SIC {sector_info.sic_code}) — "
                    "le seuil absolu 0.5 ne s'applique pas, signal forcé clean."
                ),
            },
        )

    is_alert = ratio < _FCF_NI_3Y_THRESHOLD
    return SignalResult(
        positive=is_alert,
        details={
            "ratio_3y": str(ratio),
            "threshold": str(_FCF_NI_3Y_THRESHOLD),
            "mode": "absolute_fallback",
        },
        calculation_detail={
            "formula": _S1_FORMULA,
            "variables": base_vars,
            "intermediates": {"ratio_3y": str(ratio)},
            "computation_steps": [
                f"ratio = {fcf_total} / {ni_total} = {ratio}",
                f"Seuil = {_FCF_NI_3Y_THRESHOLD} → {'alerte (< seuil)' if is_alert else 'clean (≥ seuil)'}",
            ],
            "result": str(ratio),
            "thresholds": _S1_THRESHOLDS,
            "interpretation": (
                f"ratio FCF/NI 3y = {ratio} "
                f"{'< 0.5 → alerte fraude' if is_alert else '≥ 0.5 → clean'}"
            ),
        },
    )


# ─── Signal 2 — Receivables growth anomaly ──────────────────────────────────


def _signal_receivables_anomaly(facts: dict[str, Any]) -> SignalResult:
    """Receivables CAGR 2y vs Revenue CAGR 2y; absolute fallback per §4.2.1.

    The spec compares 2-year CAGR (needs 3 entries: y-2, y-1, y0).
    """
    rcv_series = fs.extract_n_year_annuals(facts, "receivables", n=3)
    rev_series = fs.extract_n_year_annuals(facts, "revenues", n=3)

    if len(rcv_series) < 3 or len(rev_series) < 3:
        return SignalResult(
            positive=None,
            details={"reason": "insufficient receivables or revenue history (< 3 FY entries)"},
            calculation_detail={
                "formula": _S2_FORMULA,
                "variables": {"n_receivables": len(rcv_series), "n_revenue": len(rev_series)},
                "intermediates": {},
                "computation_steps": ["Historique < 3 ans (CAGR 2y requiert y-2, y-1, y0)"],
                "result": None,
                "thresholds": _S2_THRESHOLDS,
                "interpretation": "Données insuffisantes pour évaluer S2.",
            },
        )

    rcv_cagr = _cagr(rcv_series[2][1], rcv_series[0][1], 2)
    rev_cagr = _cagr(rev_series[2][1], rev_series[0][1], 2)

    base_vars = {
        "Receivables y-2": str(rcv_series[2][1]),
        "Receivables y0":   str(rcv_series[0][1]),
        "Revenue y-2":      str(rev_series[2][1]),
        "Revenue y0":       str(rev_series[0][1]),
    }

    if rev_cagr is None or rcv_cagr is None:
        return SignalResult(
            positive=None,
            details={"reason": "CAGR undefined (sign change or zero start)"},
            calculation_detail={
                "formula": _S2_FORMULA,
                "variables": base_vars,
                "intermediates": {
                    "Receivables CAGR 2y": str(rcv_cagr) if rcv_cagr else "undefined",
                    "Revenue CAGR 2y":      str(rev_cagr) if rev_cagr else "undefined",
                },
                "computation_steps": ["CAGR indéfini (changement de signe ou start nul)"],
                "result": None,
                "thresholds": _S2_THRESHOLDS,
                "interpretation": "CAGR(s) indéfini(s) — signal non significatif.",
            },
        )

    if rev_cagr <= 0:
        return SignalResult(
            positive=False,
            details={
                "reason": "Revenue CAGR ≤ 0 — anomaly check not meaningful in decline",
                "receivables_cagr_2y": str(rcv_cagr),
                "revenue_cagr_2y": str(rev_cagr),
            },
            calculation_detail={
                "formula": _S2_FORMULA,
                "variables": base_vars,
                "intermediates": {
                    "Receivables CAGR 2y": str(rcv_cagr),
                    "Revenue CAGR 2y":      str(rev_cagr),
                },
                "computation_steps": [
                    f"Revenue CAGR 2y = {rev_cagr} ≤ 0 → check non significatif en déclin"
                ],
                "result": None,
                "thresholds": _S2_THRESHOLDS,
                "interpretation": "Revenus en déclin → signal forcé clean (anomaly non testable).",
            },
        )

    ratio = rcv_cagr / rev_cagr
    is_alert = ratio > _RECEIVABLES_GROWTH_RATIO_THRESHOLD
    return SignalResult(
        positive=is_alert,
        details={
            "receivables_cagr_2y": str(rcv_cagr),
            "revenue_cagr_2y": str(rev_cagr),
            "ratio": str(ratio),
            "threshold": str(_RECEIVABLES_GROWTH_RATIO_THRESHOLD),
            "mode": "absolute_fallback",
        },
        calculation_detail={
            "formula": _S2_FORMULA,
            "variables": base_vars,
            "intermediates": {
                "Receivables CAGR 2y": str(rcv_cagr),
                "Revenue CAGR 2y":      str(rev_cagr),
                "ratio = rcv/rev":      str(ratio),
            },
            "computation_steps": [
                f"Receivables CAGR 2y = {rcv_cagr}",
                f"Revenue CAGR 2y      = {rev_cagr}",
                f"ratio = {rcv_cagr} / {rev_cagr} = {ratio}",
                f"Seuil = {_RECEIVABLES_GROWTH_RATIO_THRESHOLD} → "
                f"{'alerte (>)' if is_alert else 'clean (≤)'}",
            ],
            "result": str(ratio),
            "thresholds": _S2_THRESHOLDS,
            "interpretation": (
                f"ratio = {ratio} "
                f"{'> 2.0 → créances accélèrent vs revenus (alerte fraude)' if is_alert else '≤ 2.0 → clean'}"
            ),
        },
    )


# ─── Signal 3 — Restatements / late filings ─────────────────────────────────


def _signal_restatements(submissions: dict[str, Any] | None) -> SignalResult:
    """Count 10-K/A, 10-Q/A, NT 10-K, NT 10-Q over the past 3 years."""
    def _no_data(reason: str) -> SignalResult:
        return SignalResult(
            positive=None,
            details={"reason": reason},
            calculation_detail={
                "formula": _S3_FORMULA,
                "variables": {},
                "intermediates": {},
                "computation_steps": [reason],
                "result": None,
                "thresholds": _S3_THRESHOLDS,
                "interpretation": "Données SEC submissions indisponibles — signal indéterminé.",
            },
        )

    if not isinstance(submissions, dict):
        return _no_data("SEC submissions unavailable (non-US or fetch failed)")

    recent = submissions.get("filings", {}).get("recent")
    if not isinstance(recent, dict):
        return _no_data("submissions payload missing 'filings.recent' object")

    forms_raw = recent.get("form")
    dates_raw = recent.get("filingDate")
    if not isinstance(forms_raw, list) or not isinstance(dates_raw, list):
        return _no_data("submissions 'form' or 'filingDate' arrays missing")

    cutoff = date.today() - timedelta(days=365 * _RESTATEMENTS_LOOKBACK_YEARS)
    count = 0
    matching_forms: list[dict[str, str]] = []
    for form, filing_date_str in zip(forms_raw, dates_raw):
        if not isinstance(form, str) or form not in _RESTATEMENT_FORMS:
            continue
        try:
            filed = date.fromisoformat(str(filing_date_str))
        except (TypeError, ValueError):
            continue
        if filed >= cutoff:
            count += 1
            matching_forms.append({"form": form, "filingDate": str(filing_date_str)})

    is_alert = count >= _RESTATEMENTS_THRESHOLD
    return SignalResult(
        positive=is_alert,
        details={
            "count": count,
            "threshold": _RESTATEMENTS_THRESHOLD,
            "lookback_years": _RESTATEMENTS_LOOKBACK_YEARS,
            "matching_filings": matching_forms[:10],
        },
        calculation_detail={
            "formula": _S3_FORMULA,
            "variables": {
                "lookback_years":       str(_RESTATEMENTS_LOOKBACK_YEARS),
                "restatement_forms":    ", ".join(sorted(_RESTATEMENT_FORMS)),
                "threshold":            str(_RESTATEMENTS_THRESHOLD),
            },
            "intermediates": {"count": str(count)},
            "computation_steps": [
                f"Fenêtre = 3 ans depuis {cutoff.isoformat()}",
                f"Formulaires recherchés = {sorted(_RESTATEMENT_FORMS)}",
                f"Décompte = {count}",
                f"Seuil = {_RESTATEMENTS_THRESHOLD} → {'alerte' if is_alert else 'clean'}",
            ],
            "result": str(count),
            "thresholds": _S3_THRESHOLDS,
            "interpretation": (
                f"{count} restatement(s)/late-filing(s) sur 3 ans "
                f"{'≥ 3 → alerte fraude' if is_alert else '< 3 → clean'}"
            ),
        },
    )
