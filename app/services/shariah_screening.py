import json
from typing import Any, Optional

import pandas as pd

from app.core.logging import logger
from app.integration.anthropic_client import analyze_with_claude
from app.integration.yfinance_client import get_financials
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


def _income_stmt_to_text(income_stmt: pd.DataFrame) -> str:
    """Convert yfinance income statement DataFrame to a readable string for AI analysis."""
    if income_stmt is None or income_stmt.empty:
        return ""
    # Take the most recent column (latest fiscal year)
    latest = income_stmt.iloc[:, 0]
    lines = [f"  {row}: {val}" for row, val in latest.items() if pd.notna(val)]
    return "\n".join(lines)


async def _ai_extract_impure_revenue(symbol: str, total_revenue: Optional[float]) -> Optional[float]:
    """Use Claude to analyse the full income statement and extract impure revenue."""
    try:
        financials = await get_financials(symbol)
        income_stmt = financials.get("income_stmt")
        if income_stmt is None or income_stmt.empty:
            logger.warning("No income statement available for %s, cannot run AI extraction", symbol)
            return None

        stmt_text = _income_stmt_to_text(income_stmt)
        if not stmt_text:
            return None

        prompt = (
            f"Analyse the following income statement for {symbol} and extract the total "
            f"amount of impure (haram) revenue. Impure revenue includes:\n"
            f"- Interest income / Interest earned\n"
            f"- Gains from speculative trading\n"
            f"- Revenue from alcohol, tobacco, gambling, weapons, or adult entertainment\n"
            f"- Any other non-Shariah-compliant income\n\n"
            f"Income Statement (most recent fiscal year):\n{stmt_text}\n\n"
            f"Total Revenue reported: {total_revenue}\n\n"
            f"Return ONLY a JSON object with these fields:\n"
            f'{{"impure_revenue": <number or 0 if none found>, '
            f'"sources": [<list of line items considered impure>], '
            f'"confidence": "<high|medium|low>"}}\n'
        )

        raw = await analyze_with_claude(prompt)
        logger.info("AI impure revenue extraction for %s: %s", symbol, raw[:200])

        data = json.loads(raw)
        impure = data.get("impure_revenue")
        confidence = data.get("confidence", "low")
        sources = data.get("sources", [])

        if impure is not None and isinstance(impure, (int, float)) and impure >= 0:
            logger.info(
                "AI extracted impure_revenue=%.2f for %s (confidence=%s, sources=%s)",
                impure, symbol, confidence, sources,
            )
            return float(impure)

        logger.warning("AI returned unusable impure_revenue for %s: %s", symbol, data)
        return None

    except Exception as exc:
        logger.warning("AI impure revenue extraction failed for %s: %s", symbol, exc)
        return None


async def screen(info: dict[str, Any], symbol: str = "") -> ShariahScreening:
    """
    Double-level Shariah screening.

    AAOIFI: denominators use Market Cap.
    Strict: denominators use Total Assets (where applicable).

    Impure revenue is estimated as (totalRevenue - operatingRevenue) when
    a direct figure is unavailable — conservative proxy.
    Falls back to AI analysis of the full income statement when the proxy
    cannot be computed.
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
        impure_revenue = None

    # Fallback: use AI to extract impure revenue from the full income statement
    if impure_revenue is None and symbol:
        logger.info("Structured impure_revenue unavailable for %s, attempting AI extraction", symbol)
        impure_revenue = await _ai_extract_impure_revenue(symbol, total_revenue)

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
