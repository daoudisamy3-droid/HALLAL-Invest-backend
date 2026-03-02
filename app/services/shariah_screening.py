from typing import Any, Optional

from app.core.logging import logger
from app.models.schemas import ShariahScreening, ShariahLevel, ShariahRatio


def _safe_div(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator


def _check_ratio(
    name: str,
    numerator: Optional[float],
    denominator: Optional[float],
    threshold: float,
    num_label: str,
    den_label: str,
) -> ShariahRatio:
    value = _safe_div(numerator, denominator)
    if value is None:
        return ShariahRatio(
            name=name,
            value=None,
            threshold=threshold,
            passed=False,
            detail=f"Could not compute: {num_label} or {den_label} unavailable",
        )
    passed = value < threshold
    pct = round(value * 100, 2)
    return ShariahRatio(
        name=name,
        value=round(value, 4),
        threshold=threshold,
        passed=passed,
        detail=f"{num_label}/{den_label} = {pct}% ({'<' if passed else '>='} {threshold*100}%)",
    )


def _extract_financial_values(info: dict[str, Any]) -> dict[str, Optional[float]]:
    """Extract relevant financial values from yfinance info dict."""
    return {
        "total_debt": info.get("totalDebt"),
        "market_cap": info.get("marketCap"),
        "total_assets": info.get("totalAssets") or info.get("enterpriseValue"),
        "cash": info.get("totalCash"),
        "total_revenue": info.get("totalRevenue"),
    }


def _compute_level(
    level_name: str,
    total_debt: Optional[float],
    cash_ibs: Optional[float],
    impure_revenue: Optional[float],
    denominator: Optional[float],
    den_label: str,
    total_revenue: Optional[float],
) -> ShariahLevel:
    """Run the three-ratio screening for one level (AAOIFI or Strict)."""

    r1 = _check_ratio(
        "Debt Ratio",
        total_debt,
        denominator,
        0.33,
        "Total Debt",
        den_label,
    )
    r2 = _check_ratio(
        "Cash + Interest-Bearing Securities Ratio",
        cash_ibs,
        denominator,
        0.33,
        "Cash + IBS",
        den_label,
    )
    r3 = _check_ratio(
        "Impure Revenue Ratio",
        impure_revenue,
        total_revenue,
        0.05,
        "Impure Revenue",
        "Total Revenue",
    )

    ratios = [r1, r2, r3]
    passed = all(r.passed for r in ratios)

    return ShariahLevel(level_name=level_name, passed=passed, ratios=ratios)


def screen(info: dict[str, Any]) -> ShariahScreening:
    """
    Double-level Shariah screening.

    AAOIFI: denominators use Market Cap.
    Strict: denominators use Total Assets (where applicable).

    Impure revenue is estimated as (totalRevenue - operatingRevenue) when
    a direct figure is unavailable — conservative proxy.
    """
    logger.info("Running Shariah screening")

    vals = _extract_financial_values(info)
    total_debt = vals["total_debt"]
    market_cap = vals["market_cap"]
    total_assets = vals["total_assets"]
    cash = vals["cash"]
    total_revenue = vals["total_revenue"]

    # Conservative proxy for impure (haram) revenue:
    # difference between total revenue and operating revenue,
    # which captures interest income, speculative gains, etc.
    operating_revenue = info.get("operatingRevenue")
    if total_revenue is not None and operating_revenue is not None and operating_revenue > 0:
        impure_revenue = max(total_revenue - operating_revenue, 0)
    else:
        # If we can't determine, set to None so the ratio flags as unavailable
        impure_revenue = None

    # AAOIFI Level — denominator: Market Cap
    aaoifi = _compute_level(
        "AAOIFI",
        total_debt,
        cash,
        impure_revenue,
        market_cap,
        "Market Cap",
        total_revenue,
    )

    # Strict Level — denominator: Total Assets (except impure revenue still uses total revenue)
    strict = _compute_level(
        "Strict",
        total_debt,
        cash,
        impure_revenue,
        total_assets,
        "Total Assets",
        total_revenue,
    )

    # Halal Badge determination
    if aaoifi.passed and strict.passed:
        badge = "PASS"
        summary = "Stock passes both AAOIFI and Strict Shariah screens."
    elif aaoifi.passed:
        badge = "DOUBTFUL"
        summary = "Stock passes AAOIFI but fails the Strict screen. Consult a scholar."
    else:
        badge = "FAIL"
        summary = "Stock fails the AAOIFI Shariah screen."

    return ShariahScreening(
        halal_badge=badge,
        aaoifi=aaoifi,
        strict=strict,
        summary=summary,
    )
