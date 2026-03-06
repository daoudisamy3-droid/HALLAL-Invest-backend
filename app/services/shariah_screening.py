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
            passed=None,
            detail=f"Data unavailable: {num_label} or {den_label} is N/A",
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


def _compute_level(
    level_name: str,
    total_debt: Optional[float],
    cash: Optional[float],
    impure_revenue: Optional[float],
    denominator: Optional[float],
    den_label: str,
    total_revenue: Optional[float],
) -> ShariahLevel:
    """Run the three-ratio screening for one level (AAOIFI or Strict)."""

    r1 = _check_ratio("Debt Ratio", total_debt, denominator, 0.33, "Total Debt", den_label)
    r2 = _check_ratio("Cash + Interest-Bearing Securities Ratio", cash, denominator, 0.33, "Cash + IBS", den_label)
    r3 = _check_ratio("Impure Revenue Ratio", impure_revenue, total_revenue, 0.05, "Impure Revenue", "Total Revenue")

    ratios = [r1, r2, r3]

    # If any ratio has passed=None (data unavailable), the level is inconclusive
    if any(r.passed is None for r in ratios):
        passed = None
    else:
        passed = all(r.passed for r in ratios)

    return ShariahLevel(level_name=level_name, passed=passed, ratios=ratios)


def screen(info: dict[str, Any]) -> ShariahScreening:
    """
    Double-level Shariah screening using only raw financial data.

    AAOIFI: denominators use Market Cap.
    Strict: denominators use Total Assets.

    Impure revenue is estimated as (totalRevenue - operatingRevenue) when
    available. If the data is missing, the ratio is marked N/A — never
    estimated or filled by AI.
    """
    logger.info("Running Shariah screening (data-only, no AI)")

    total_debt = info.get("totalDebt")
    market_cap = info.get("marketCap")
    total_assets = info.get("totalAssets") or info.get("enterpriseValue")
    cash = info.get("totalCash")
    total_revenue = info.get("totalRevenue")

    # Conservative proxy for impure (haram) revenue from raw data only.
    # If operating revenue is unavailable, impure_revenue stays None (N/A).
    operating_revenue = info.get("operatingRevenue")
    if total_revenue is not None and operating_revenue is not None and operating_revenue > 0:
        impure_revenue: Optional[float] = max(total_revenue - operating_revenue, 0)
    else:
        impure_revenue = None

    # AAOIFI Level — denominator: Market Cap
    aaoifi = _compute_level("AAOIFI", total_debt, cash, impure_revenue, market_cap, "Market Cap", total_revenue)

    # Strict Level — denominator: Total Assets
    strict = _compute_level("Strict", total_debt, cash, impure_revenue, total_assets, "Total Assets", total_revenue)

    # Badge determination
    if aaoifi.passed is None or strict.passed is None:
        badge = "INCONCLUSIVE"
        summary = "Insufficient data for definitive Shariah screening. Some ratios could not be computed."
    elif aaoifi.passed and strict.passed:
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
