"""
Altman Z-Score — bankruptcy risk model (public company version).

Formula: Z = 1.2*X1 + 1.4*X2 + 3.3*X3 + 0.6*X4 + 1.0*X5

  X1 = working_capital / total_assets
  X2 = retained_earnings / total_assets
  X3 = EBIT / total_assets
  X4 = market_cap / total_liabilities
  X5 = revenue / total_assets

Zones:
  Z ≥ 2.99 → SÛRE (safe)
  1.81 ≤ Z < 2.99 → GRISE (grey zone)
  Z < 1.81 → DANGEREUSE (distress)
"""

from typing import Optional


def _zone(z: float) -> str:
    if z >= 2.99:
        return "SÛRE"
    if z >= 1.81:
        return "GRISE"
    return "DANGEREUSE"


def compute_altman(data: dict) -> dict:
    """
    Compute Altman Z-Score from scoring data dict.

    Returns:
        {
            "z_score": float | None,
            "zone": str | None,
            "normalized": float,
            "components": {"x1": ..., "x2": ..., "x3": ..., "x4": ..., "x5": ...},
            "available": bool,
        }
    """
    total_assets: Optional[float] = data.get("total_assets")
    current_assets: Optional[float] = data.get("current_assets")
    current_liabilities: Optional[float] = data.get("current_liabilities")
    retained_earnings: Optional[float] = data.get("retained_earnings")
    ebit: Optional[float] = data.get("ebit")
    market_cap: Optional[float] = data.get("market_cap")
    total_liabilities: Optional[float] = data.get("total_liabilities")
    revenue: Optional[float] = data.get("revenue")

    available = (
        total_assets is not None
        and total_assets > 0
        and revenue is not None
    )

    if not available:
        return {
            "z_score": None,
            "zone": None,
            "normalized": 50.0,
            "components": {"x1": None, "x2": None, "x3": None, "x4": None, "x5": None},
            "available": False,
        }

    working_capital: Optional[float] = None
    if current_assets is not None and current_liabilities is not None:
        working_capital = current_assets - current_liabilities

    x1: Optional[float] = working_capital / total_assets if working_capital is not None else None
    x2: Optional[float] = retained_earnings / total_assets if retained_earnings is not None else None
    x3: Optional[float] = ebit / total_assets if ebit is not None else None
    x4: Optional[float] = (
        market_cap / total_liabilities
        if market_cap is not None and total_liabilities and total_liabilities > 0
        else None
    )
    x5: Optional[float] = revenue / total_assets if revenue is not None else None

    # Use 0.0 for missing components (conservative)
    z = (
        1.2 * (x1 or 0.0)
        + 1.4 * (x2 or 0.0)
        + 3.3 * (x3 or 0.0)
        + 0.6 * (x4 or 0.0)
        + 1.0 * (x5 or 0.0)
    )

    # Normalize Z → 0-100
    from app.ml.scoring.normalizer import normalize_altman
    normalized = normalize_altman(z)

    return {
        "z_score": round(z, 3),
        "zone": _zone(z),
        "normalized": round(normalized, 1),
        "components": {
            "x1": round(x1, 4) if x1 is not None else None,
            "x2": round(x2, 4) if x2 is not None else None,
            "x3": round(x3, 4) if x3 is not None else None,
            "x4": round(x4, 4) if x4 is not None else None,
            "x5": round(x5, 4) if x5 is not None else None,
        },
        "available": True,
    }
