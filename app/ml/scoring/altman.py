"""
Altman Z-Score — bankruptcy risk model.

Two variants:
  Canonical Z (all 5 components):
    Z = 1.2·X1 + 1.4·X2 + 3.3·X3 + 0.6·X4 + 1.0·X5
    Zones: ≥ 2.99 SÛRE | 1.81–2.99 GRISE | < 1.81 DANGEREUSE

  Z'' (Altman 1993, non-manufacturers, when X5 is unavailable):
    Z'' = 6.56·X1 + 3.26·X2 + 6.72·X3 + 1.05·X4
    Zones: ≥ 2.6 SÛRE | 1.1–2.6 GRISE | < 1.1 DANGEREUSE

Components:
  X1 = working_capital / total_assets
  X2 = retained_earnings / total_assets
  X3 = EBIT / total_assets
  X4 = market_cap / total_liabilities
  X5 = revenue / total_assets  (canonical Z only)

Returns available=False if fewer than X1–X4 are computable.
"""

from typing import Optional

from app.ml.scoring.normalizer import normalize_altman


def _zone_canonical(z: float) -> str:
    if z >= 2.99:
        return "SÛRE"
    if z >= 1.81:
        return "GRISE"
    return "DANGEREUSE"


def _zone_zprime(z: float) -> str:
    if z >= 2.6:
        return "SÛRE"
    if z >= 1.1:
        return "GRISE"
    return "DANGEREUSE"


def _normalize_zprime(z: float) -> float:
    """Z'' → 0-100 using Z'' thresholds (2.6/1.1)."""
    from app.ml.scoring.normalizer import _clamp, normalize_linear
    if z >= 2.6:
        return _clamp(normalize_linear(z, 2.6, 5.0) * 0.1 + 90.0)
    if z >= 1.1:
        return _clamp(normalize_linear(z, 1.1, 2.6) * 0.5 + 40.0)
    return _clamp(normalize_linear(z, -2.0, 1.1) * 0.4)


def compute_altman(data: dict) -> dict:
    """
    Compute Altman Z-Score from scoring data dict.

    Returns:
        {
            "z_score": float | None,
            "zone": str | None,
            "variant": "Z" | "Z''",
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

    _unavailable = {
        "z_score": None, "zone": None, "variant": None,
        "normalized": 50.0,
        "components": {"x1": None, "x2": None, "x3": None, "x4": None, "x5": None},
        "available": False,
    }

    if not total_assets or total_assets <= 0:
        return _unavailable

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

    components = {
        "x1": round(x1, 4) if x1 is not None else None,
        "x2": round(x2, 4) if x2 is not None else None,
        "x3": round(x3, 4) if x3 is not None else None,
        "x4": round(x4, 4) if x4 is not None else None,
        "x5": round(x5, 4) if x5 is not None else None,
    }

    # ── Canonical Z (all 5 components) ───────────────────────────
    if all(v is not None for v in (x1, x2, x3, x4, x5)):
        z = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5
        return {
            "z_score": round(z, 3),
            "zone": _zone_canonical(z),
            "variant": "Z",
            "normalized": round(normalize_altman(z), 1),
            "components": components,
            "available": True,
        }

    # ── Z'' (X1–X4 only; X5 unavailable) ─────────────────────────
    if all(v is not None for v in (x1, x2, x3, x4)):
        z_pp = 6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4
        return {
            "z_score": round(z_pp, 3),
            "zone": _zone_zprime(z_pp),
            "variant": "Z''",
            "normalized": round(_normalize_zprime(z_pp), 1),
            "components": components,
            "available": True,
        }

    # ── Insufficient data ─────────────────────────────────────────
    return {**_unavailable, "components": components}
