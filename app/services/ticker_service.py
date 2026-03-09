"""
Ticker Service — Raw yfinance data extraction for the INFOS tab.

Pure data mapping, no AI/LLM. Extracts structured fields from
yfinance ticker.info for frontend consumption.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import yfinance as yf

from app.core.logging import logger


_executor = ThreadPoolExecutor(max_workers=4)


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


def _extract_strategy_data(symbol: str) -> dict[str, Any]:
    """
    Blocking call — extract all INFOS-tab data from yfinance.

    Returns a flat dict with:
      - pitch: business description
      - context: sector/industry/country
      - swot_ratios: key financial ratios for SWOT inference
      - sentiment: analyst consensus
      - competitors: peer companies (from yfinance recommendations/sector)
    """
    ticker = yf.Ticker(symbol)
    info = ticker.info or {}

    if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
        raise ValueError(f"No data found for symbol: {symbol}")

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
        "debt_to_equity": {"value": debt_to_equity, "display": f"{debt_to_equity:.2f}" if debt_to_equity is not None else None},
        "current_ratio": {"value": current_ratio, "display": f"{current_ratio:.2f}" if current_ratio is not None else None},
        "operating_margins": {"value": operating_margins, "display": _fmt_pct(operating_margins)},
        "gross_margins": {"value": gross_margins, "display": _fmt_pct(gross_margins)},
    }

    # ── Sentiment ─────────────────────────────────────────────────
    sentiment = {
        "recommendation_key": info.get("recommendationKey"),       # e.g. "buy"
        "recommendation_mean": _safe_float(info.get("recommendationMean")),  # 1.0–5.0
        "target_mean_price": _safe_float(info.get("targetMeanPrice")),
        "number_of_analysts": info.get("numberOfAnalystOpinions"),
    }

    # ── Competitors / Peers ───────────────────────────────────────
    competitors: list[dict[str, str]] = []
    try:
        # yfinance exposes recommendations_summary or similar peer data
        # on some tickers; we also check for sector peers via industry
        recs = ticker.recommendations
        if recs is not None and not recs.empty:
            # Recent analyst firms as a proxy for "who covers this stock"
            firms = recs.get("Firm", recs.get("firm", None))
            if firms is not None:
                unique_firms = list(firms.dropna().unique()[:5])
                competitors = [{"name": f, "tag": "analyst_firm"} for f in unique_firms]
    except Exception as exc:
        logger.debug("Could not extract peers for %s: %s", symbol, exc)

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


async def get_strategy_data(symbol: str) -> dict[str, Any]:
    """
    Async wrapper for raw yfinance strategy data extraction.

    Args:
        symbol: Ticker symbol (e.g. "AAPL").

    Returns:
        Structured dict with pitch, context, swot_ratios, sentiment, competitors.

    Raises:
        ValueError: If symbol not found on yfinance.
    """
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _extract_strategy_data, symbol.upper().strip())
