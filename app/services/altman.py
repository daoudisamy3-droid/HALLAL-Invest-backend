"""Altman Z-Score gate (Step 4 — bankruptcy risk).

Spec authority: §4.2.1 Gate 2.

V1 uses Z'' (4-factor, no market-cap term) for **all** companies (Q2
plan validated). The Z classique (5 factors with MC/TL) requires a
live equity price feed which we don't have in V1 (no YFinance/Alpaca
integration yet). The spec explicitly documents Z'' as the variant
"pour entreprises non-manufacturières"; we apply it uniformly here and
will reintroduce the Z/Z'' split when a price source lands (step 5+).

Formula::

    Z'' = 6.56·(WC/TA) + 3.26·(RE/TA) + 6.72·(EBIT/TA) + 1.05·(BV/TL)

Components (all extracted from SEC EDGAR company_facts):
  WC   = current_assets − current_liabilities    (working capital)
  RE   = retained_earnings                       (retained earnings)
  EBIT = operating_income                        (V1 proxy — see note)
  BV   = stockholders_equity                     (book value)
  TL   = total_liabilities
  TA   = total_assets

Note on EBIT proxy: the spec uses EBIT (Earnings Before Interest and
Taxes). SEC GAAP doesn't tag EBIT directly — companies report
``OperatingIncomeLoss`` (income from operations) which is close to but
not strictly EBIT. We use it as a deliberate V1 approximation;
divergence is typically < 5 % for non-financial companies.

Verdict thresholds (per spec):
  Z'' ≥ 2.6   : SAFE      → PASS
  1.1–2.6     : GREY      → WARNING (passes the gate, surfaces orange banner)
  Z'' < 1.1   : DISTRESS  → FAIL
  any input None → INSUFFICIENT_DATA
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from app.services import financials_service as fs


# Coefficients (spec §4.2.1)
_COEF_WC = Decimal("6.56")
_COEF_RE = Decimal("3.26")
_COEF_EBIT = Decimal("6.72")
_COEF_BV = Decimal("1.05")

# Thresholds
_THRESHOLD_SAFE = Decimal("2.6")
_THRESHOLD_DISTRESS = Decimal("1.1")


AltmanVerdict = Literal["PASS", "WARNING", "FAIL", "INSUFFICIENT_DATA"]
AltmanZone = Literal["SAFE", "GREY", "DISTRESS", "N/A"]


_ALTMAN_FORMULA = (
    "Z'' = 6.56·(WC/TA) + 3.26·(RE/TA) + 6.72·(EBIT/TA) + 1.05·(BV/TL)"
)
_ALTMAN_THRESHOLDS = [
    {"label": "SAFE",     "condition": "Z'' ≥ 2.6"},
    {"label": "GREY",     "condition": "1.1 ≤ Z'' < 2.6"},
    {"label": "DISTRESS", "condition": "Z'' < 1.1"},
]


@dataclass(frozen=True)
class AltmanResult:
    verdict: AltmanVerdict
    z_score: Decimal | None
    zone: AltmanZone
    inputs: dict[str, Decimal | None]
    reason: str | None
    calculation_detail: dict[str, Any]


def compute(facts: dict[str, Any]) -> AltmanResult:
    """Compute Z'' from a SEC company_facts payload."""
    ca = fs.extract_latest_annual_value(facts, "current_assets")
    cl = fs.extract_latest_annual_value(facts, "current_liabilities")
    re_ = fs.extract_latest_annual_value(facts, "retained_earnings")
    ebit = fs.extract_latest_annual_value(facts, "operating_income")
    bv = fs.extract_latest_annual_value(facts, "stockholders_equity")
    tl = fs.extract_latest_annual_value(facts, "total_liabilities")
    ta = fs.extract_latest_annual_value(facts, "total_assets")

    wc: Decimal | None = None
    if ca is not None and cl is not None:
        wc = ca - cl

    inputs: dict[str, Decimal | None] = {
        "working_capital": wc,
        "retained_earnings": re_,
        "operating_income": ebit,
        "book_value": bv,
        "total_liabilities": tl,
        "total_assets": ta,
    }

    variables: dict[str, str | None] = {
        "WC (working capital = current_assets − current_liabilities)":
            str(wc) if wc is not None else None,
        "RE (retained_earnings)": str(re_) if re_ is not None else None,
        "EBIT (operating_income proxy)": str(ebit) if ebit is not None else None,
        "BV (stockholders_equity)": str(bv) if bv is not None else None,
        "TL (total_liabilities)": str(tl) if tl is not None else None,
        "TA (total_assets)": str(ta) if ta is not None else None,
    }

    missing = [k for k, v in inputs.items() if v is None]
    if missing or ta is None or ta == Decimal("0") or tl is None or tl == Decimal("0"):
        reason_missing = (
            "Données SEC EDGAR insuffisantes pour calculer Z'' : "
            f"manquant ou nul → {', '.join(missing) or 'TA / TL = 0'}."
        )
        return AltmanResult(
            verdict="INSUFFICIENT_DATA",
            z_score=None,
            zone="N/A",
            inputs=inputs,
            reason=reason_missing,
            calculation_detail={
                "formula": _ALTMAN_FORMULA,
                "variables": variables,
                "intermediates": {},
                "computation_steps": [],
                "result": None,
                "thresholds": _ALTMAN_THRESHOLDS,
                "interpretation": reason_missing,
            },
        )

    # mypy/pyright narrowing — `missing` empty implies all values present.
    assert wc is not None and re_ is not None and ebit is not None
    assert bv is not None and tl is not None and ta is not None

    r_wc = wc / ta
    r_re = re_ / ta
    r_ebit = ebit / ta
    r_bv = bv / tl

    term1 = _COEF_WC * r_wc
    term2 = _COEF_RE * r_re
    term3 = _COEF_EBIT * r_ebit
    term4 = _COEF_BV * r_bv
    z = term1 + term2 + term3 + term4

    if z >= _THRESHOLD_SAFE:
        verdict: AltmanVerdict = "PASS"
        zone: AltmanZone = "SAFE"
        reason = None
        interp = f"Z'' = {z} ≥ 2.6 → zone SAFE → PASS"
    elif z >= _THRESHOLD_DISTRESS:
        verdict = "WARNING"
        zone = "GREY"
        reason = (
            f"Z'' = {z} dans la zone grise [1.1, 2.6) — gate passé "
            "mais à surveiller (banderole orange)."
        )
        interp = f"Z'' = {z} ∈ [1.1, 2.6) → zone GREY → WARNING"
    else:
        verdict = "FAIL"
        zone = "DISTRESS"
        reason = f"Z'' = {z} < 1.1 — zone de distress, risque de faillite élevé."
        interp = f"Z'' = {z} < 1.1 → zone DISTRESS → FAIL"

    calculation_detail = {
        "formula": _ALTMAN_FORMULA,
        "variables": variables,
        "intermediates": {
            "WC/TA":    str(r_wc),
            "RE/TA":    str(r_re),
            "EBIT/TA":  str(r_ebit),
            "BV/TL":    str(r_bv),
        },
        "computation_steps": [
            f"6.56 × {r_wc} = {term1}",
            f"3.26 × {r_re} = {term2}",
            f"6.72 × {r_ebit} = {term3}",
            f"1.05 × {r_bv} = {term4}",
            f"Sum = {z}",
        ],
        "result": str(z),
        "thresholds": _ALTMAN_THRESHOLDS,
        "interpretation": interp,
    }

    return AltmanResult(
        verdict=verdict,
        z_score=z,
        zone=zone,
        inputs=inputs,
        reason=reason,
        calculation_detail=calculation_detail,
    )
