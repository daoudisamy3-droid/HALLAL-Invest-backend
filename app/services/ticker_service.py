"""
Ticker Service — Raw yfinance data extraction for the INFOS tab.

Pure data mapping, no AI/LLM. Uses the shared yfinance_client
to extract structured fields for frontend consumption.
"""

from typing import Any, Optional

from app.core.logging import logger
from app.integration.yfinance_client import get_ticker_info


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _fmt_pct(val: Optional[float]) -> Optional[str]:
    """Format ratio as percentage string (0.15 → '15.00%')."""
    if val is None:
        return None
    return f"{val * 100:.2f}%"


def _fmt_num(val: Optional[float]) -> Optional[str]:
    if val is None:
        return None
    return f"{val:.2f}"


async def get_strategy_data(symbol: str) -> dict[str, Any]:
    """
    Extract all INFOS-tab data from yfinance via the shared client.

    Returns:
        {
            "symbol", "company_name",
            "pitch": longBusinessSummary,
            "context": {sector, industry, country, ...},
            "swot_ratios": {profit_margins, roe, revenue_growth, ...},
            "sentiment": {recommendation_key, recommendation_mean, ...},
            "competitors": [...],
            "market_data": {market_cap, current_price, beta},
            "source": "yfinance"
        }
    """
    symbol = symbol.upper().strip()

    info = await get_ticker_info(symbol)

    # ── Pitch ─────────────────────────────────────────────────────
    pitch = info.get("longBusinessSummary")

    # ── Context ───────────────────────────────────────────────────
    context = {
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "country": info.get("country"),
        "full_time_employees": info.get("fullTimeEmployees"),
        "website": info.get("website"),
    }

    # ── SWOT Ratios ───────────────────────────────────────────────
    profit_margins = _safe_float(info.get("profitMargins"))
    roe = _safe_float(info.get("returnOnEquity"))
    revenue_growth = _safe_float(info.get("revenueGrowth"))
    earnings_growth = _safe_float(info.get("earningsGrowth"))
    debt_to_equity = _safe_float(info.get("debtToEquity"))
    current_ratio = _safe_float(info.get("currentRatio"))
    operating_margins = _safe_float(info.get("operatingMargins"))
    gross_margins = _safe_float(info.get("grossMargins"))

    swot_ratios = {
        "profit_margins": {"value": profit_margins, "display": _fmt_pct(profit_margins)},
        "return_on_equity": {"value": roe, "display": _fmt_pct(roe)},
        "revenue_growth": {"value": revenue_growth, "display": _fmt_pct(revenue_growth)},
        "earnings_growth": {"value": earnings_growth, "display": _fmt_pct(earnings_growth)},
        "debt_to_equity": {"value": debt_to_equity, "display": _fmt_num(debt_to_equity)},
        "current_ratio": {"value": current_ratio, "display": _fmt_num(current_ratio)},
        "operating_margins": {"value": operating_margins, "display": _fmt_pct(operating_margins)},
        "gross_margins": {"value": gross_margins, "display": _fmt_pct(gross_margins)},
    }

    # ── Sentiment ─────────────────────────────────────────────────
    sentiment = {
        "recommendation_key": info.get("recommendationKey"),
        "recommendation_mean": _safe_float(info.get("recommendationMean")),
        "target_mean_price": _safe_float(info.get("targetMeanPrice")),
        "number_of_analysts": info.get("numberOfAnalystOpinions"),
    }

    # ── Competitors / Peers ───────────────────────────────────────
    # yfinance doesn't expose a direct peers list in .info,
    # but some builds include companyOfficers or related fields.
    # We return an empty list — the frontend can fill this from /strategy (Gemini).
    competitors: list[dict[str, str]] = []

    # ── Market data for context ───────────────────────────────────
    market_cap = _safe_float(info.get("marketCap"))
    current_price = _safe_float(info.get("currentPrice")) or _safe_float(info.get("regularMarketPrice"))
    beta = _safe_float(info.get("beta"))

    result = {
        "symbol": symbol,
        "company_name": info.get("longName") or info.get("shortName"),
        "pitch": pitch,
        "context": context,
        "swot_ratios": swot_ratios,
        "sentiment": sentiment,
        "competitors": competitors,
        "market_data": {
            "market_cap": market_cap,
            "current_price": current_price,
            "beta": beta,
        },
        "source": "yfinance",
    }

    logger.info(
        "Ticker service for %s: pitch=%d chars, sector=%s, recommendation=%s",
        symbol,
        len(pitch) if pitch else 0,
        context.get("sector"),
        sentiment.get("recommendation_key"),
    )

    return result
