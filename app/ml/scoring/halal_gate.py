"""
Halal Gate — AAOIFI-aligned compliance screen.

Three checks:
  1. Sector/industry keywords — precise phrases to avoid false positives
     (e.g. "commercial bank" not "bank", "conventional insurance" not "insurance")
  2. Debt ratio: total_debt / market_cap ≤ 30%
  3. Cash ratio: cash / market_cap ≤ 30% (guards against interest-bearing assets)

Note: debt ratio uses total_debt vs market_cap as a proxy for the AAOIFI
36-month average total assets denominator, which is not available via yfinance.
This is an approximation; a full AAOIFI screen requires audited financials.

Returns compliant=None when key financial data is missing (incomplete_data=True),
distinct from compliant=False (confirmed violation).
"""

from typing import Optional

# Exact terms — clear-cut haram categories
_HARAM_EXACT = [
    "alcohol", "beer", "wine", "spirits", "liquor", "brewing", "winery", "distillery",
    "tobacco", "cigarette", "cigar",
    "gambling", "casino", "betting", "lottery", "sportsbook",
    "weapons", "ammunition", "firearms",
    "pork", "swine",
    "adult entertainment", "pornograph",
    "cannabis", "marijuana",
]

# Precise phrases — interest-based finance (riba) — avoids matching
# "Benchmark", "Islamic bank", "takaful insurance", "data bank", etc.
_HARAM_PHRASES = [
    "commercial bank",
    "investment bank",
    "retail bank",
    "savings bank",
    "mortgage bank",
    "consumer credit",
    "payday loan",
    "predatory lend",
    "conventional insurance",
    "life insurance company",
    "usury",
]


def _keyword_hit(text: Optional[str]) -> Optional[str]:
    """Return the matched keyword/phrase if text contains a haram term, else None."""
    if not text:
        return None
    lower = text.lower()
    for term in _HARAM_EXACT:
        if term in lower:
            return term
    for phrase in _HARAM_PHRASES:
        if phrase in lower:
            return phrase
    return None


def compute_halal_gate(data: dict) -> dict:
    """
    Evaluate AAOIFI compliance for a ticker.

    Args:
        data: flat scoring dict from data_fetcher.fetch_scoring_data()

    Returns:
        {
            "compliant": bool | None,       # None = insufficient data to decide
            "incomplete_data": bool,        # True when key financials are missing
            "reason": str | None,           # first failing reason
            "debt_ratio": float | None,     # total_debt / market_cap
            "cash_ratio": float | None,     # cash / market_cap
            "sector": str | None,
            "industry": str | None,
        }
    """
    sector: Optional[str] = data.get("sector")
    industry: Optional[str] = data.get("industry")
    company_name: Optional[str] = data.get("company_name")
    market_cap: Optional[float] = data.get("market_cap")
    total_debt: Optional[float] = data.get("total_debt")
    cash: Optional[float] = data.get("cash")

    reason: Optional[str] = None

    # ── 1. Sector keyword check ───────────────────────────────────
    for text_field in (sector, industry, company_name):
        hit = _keyword_hit(text_field)
        if hit:
            reason = f"Secteur exclu: '{hit}' détecté dans '{text_field}'"
            return {
                "compliant": False,
                "incomplete_data": False,
                "reason": reason,
                "debt_ratio": None,
                "cash_ratio": None,
                "sector": sector,
                "industry": industry,
            }

    # ── 2. Financial ratios ───────────────────────────────────────
    debt_ratio: Optional[float] = None
    cash_ratio: Optional[float] = None

    # Determine if we have enough data to make a financial determination
    has_market_cap = market_cap is not None and market_cap > 0
    has_any_ratio_data = total_debt is not None or cash is not None
    incomplete_data = not has_market_cap or not has_any_ratio_data

    if has_market_cap:
        if total_debt is not None:
            debt_ratio = abs(total_debt) / market_cap
            if debt_ratio > 0.30:
                reason = f"Ratio dette/capitalisation trop élevé: {debt_ratio:.1%} > 30%"
                return {
                    "compliant": False,
                    "incomplete_data": False,
                    "reason": reason,
                    "debt_ratio": round(debt_ratio, 4),
                    "cash_ratio": None,
                    "sector": sector,
                    "industry": industry,
                }

        if cash is not None:
            cash_ratio = abs(cash) / market_cap
            if cash_ratio > 0.30:
                reason = f"Ratio liquidités/capitalisation trop élevé: {cash_ratio:.1%} > 30%"
                return {
                    "compliant": False,
                    "incomplete_data": False,
                    "reason": reason,
                    "debt_ratio": round(debt_ratio, 4) if debt_ratio is not None else None,
                    "cash_ratio": round(cash_ratio, 4),
                    "sector": sector,
                    "industry": industry,
                }

    # If data is incomplete, we cannot confirm compliance
    compliant: Optional[bool] = None if incomplete_data else True
    if incomplete_data:
        reason = "Données financières insuffisantes pour confirmer la conformité AAOIFI"

    return {
        "compliant": compliant,
        "incomplete_data": incomplete_data,
        "reason": reason,
        "debt_ratio": round(debt_ratio, 4) if debt_ratio is not None else None,
        "cash_ratio": round(cash_ratio, 4) if cash_ratio is not None else None,
        "sector": sector,
        "industry": industry,
    }
