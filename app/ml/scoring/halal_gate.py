"""
Halal Gate — AAOIFI-aligned compliance screen.

Three checks:
  1. Sector/industry keywords (alcohol, tobacco, gambling, weapons, pork, adult, riba)
  2. Debt ratio: total_debt / market_cap ≤ 30%
  3. Cash ratio: cash / market_cap ≤ 30% (guards against interest-bearing assets)

Returns a gate result; non-compliant companies are excluded from composite scoring.
"""

from typing import Optional

_HARAM_SECTOR_KEYWORDS = [
    "alcohol", "beer", "wine", "spirits", "liquor", "brewing", "distill",
    "tobacco", "cigarette",
    "gambling", "casino", "betting", "lottery",
    "weapon", "defense", "arms", "ammunition", "military",
    "pork", "swine",
    "adult entertainment", "pornograph",
    "cannabis", "marijuana",
    "bank", "insurance", "financial services", "mortgage", "lending",
    "riba", "interest income",
]

_EXCLUDED_SECTORS = {
    "consumer defensive",  # may include tobacco / alcohol
}

_BANK_SECTORS = {
    "financial services",
    "banks",
    "insurance",
}


def _keyword_hit(text: Optional[str]) -> Optional[str]:
    """Return the matched keyword if text contains a haram keyword, else None."""
    if not text:
        return None
    lower = text.lower()
    for kw in _HARAM_SECTOR_KEYWORDS:
        if kw in lower:
            return kw
    return None


def compute_halal_gate(data: dict) -> dict:
    """
    Evaluate AAOIFI compliance for a ticker.

    Args:
        data: flat scoring dict from data_fetcher.fetch_scoring_data()

    Returns:
        {
            "compliant": bool,
            "reason": str | None,          # first failing reason
            "debt_ratio": float | None,    # total_debt / market_cap
            "cash_ratio": float | None,    # cash / market_cap
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
                "reason": reason,
                "debt_ratio": None,
                "cash_ratio": None,
                "sector": sector,
                "industry": industry,
            }

    # ── 2. Financial ratios ───────────────────────────────────────
    debt_ratio: Optional[float] = None
    cash_ratio: Optional[float] = None

    if market_cap and market_cap > 0:
        if total_debt is not None:
            debt_ratio = abs(total_debt) / market_cap
            if debt_ratio > 0.30:
                reason = f"Ratio dette/capitalisation trop élevé: {debt_ratio:.1%} > 30%"
                return {
                    "compliant": False,
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
                    "reason": reason,
                    "debt_ratio": round(debt_ratio, 4) if debt_ratio is not None else None,
                    "cash_ratio": round(cash_ratio, 4),
                    "sector": sector,
                    "industry": industry,
                }

    return {
        "compliant": True,
        "reason": None,
        "debt_ratio": round(debt_ratio, 4) if debt_ratio is not None else None,
        "cash_ratio": round(cash_ratio, 4) if cash_ratio is not None else None,
        "sector": sector,
        "industry": industry,
    }
