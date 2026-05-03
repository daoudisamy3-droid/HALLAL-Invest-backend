"""
Market Context — macro environment + sector dynamics for any ticker.

Two data layers:
  1. FRED API (macro): fed_rate, CPI, GDP growth, unemployment, credit spread
  2. YFinance (sector): ETF proxy performance, commodity, relative strength vs stock

Cache TTL: 1 hour (macro data does not change intraday).
Namespace: "market_context".

FRED API key is optional: set FRED_API_KEY in environment.
If absent, macro block returns neutral defaults (score=50).
"""

import asyncio
from typing import Optional

import httpx
import yfinance as yf

from app.core.cache import cache_get, cache_set
from app.core.config import get_settings
from app.core.logging import logger

_CTX_NS = "market_context"
_CTX_TTL = 3600  # 1 hour

FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"

FRED_SERIES = {
    "fed_rate": "FEDFUNDS",
    "cpi_yoy": "CPIAUCSL",
    "gdp_growth": "A191RL1Q225SBEA",
    "unemployment": "UNRATE",
    "credit_spread": "BAMLH0A0HYM2",
}

SECTOR_PROXIES: dict[str, dict] = {
    "Energy": {
        "etf": "XLE",
        "commodity": "CL=F",
        "commodity_name": "Pétrole WTI",
    },
    "Basic Materials": {
        "etf": "XLB",
        "commodity": "HG=F",
        "commodity_name": "Cuivre",
    },
    "Technology": {"etf": "XLK", "commodity": None, "commodity_name": None},
    "Healthcare": {"etf": "XLV", "commodity": None, "commodity_name": None},
    "Financials": {"etf": "XLF", "commodity": None, "commodity_name": None},
    "Consumer Discretionary": {"etf": "XLY", "commodity": None, "commodity_name": None},
    "Consumer Staples": {"etf": "XLP", "commodity": None, "commodity_name": None},
    "Industrials": {"etf": "XLI", "commodity": None, "commodity_name": None},
    "Real Estate": {"etf": "XLRE", "commodity": None, "commodity_name": None},
    "Utilities": {"etf": "XLU", "commodity": None, "commodity_name": None},
    "Communication Services": {"etf": "XLC", "commodity": None, "commodity_name": None},
}

_DEFAULT_PROXY = {"etf": "SPY", "commodity": None, "commodity_name": None}


# ── FRED macro ────────────────────────────────────────────────────

async def fetch_fred_macro() -> dict:
    """
    Fetches latest macro indicators from FRED.
    Returns neutral defaults if FRED_API_KEY is absent or any call fails.
    Cache TTL 1h.
    """
    cached = cache_get(_CTX_NS, "fred_macro")
    if cached is not None:
        return cached

    api_key = get_settings().fred_api_key
    if not api_key:
        logger.info("market_context: FRED_API_KEY absent — returning neutral macro defaults")
        result = _neutral_macro()
        cache_set(_CTX_NS, "fred_macro", result, ttl=_CTX_TTL)
        return result

    result: dict = {}

    async with httpx.AsyncClient(timeout=10.0) as client:
        for key, series_id in FRED_SERIES.items():
            try:
                r = await client.get(
                    FRED_BASE,
                    params={
                        "series_id": series_id,
                        "api_key": api_key,
                        "file_type": "json",
                        "limit": 2,
                        "sort_order": "desc",
                        "observation_start": "2020-01-01",
                    },
                )
                if r.status_code == 200:
                    obs = r.json().get("observations", [])
                    if obs:
                        val = obs[0].get("value", ".")
                        prev = obs[1].get("value", ".") if len(obs) > 1 else "."
                        val_f = float(val) if val != "." else None
                        prev_f = float(prev) if prev != "." else None
                        trend = (
                            "hausse" if val_f is not None and prev_f is not None and val_f > prev_f
                            else "baisse"
                        )
                        result[key] = {
                            "value": val_f,
                            "previous": prev_f,
                            "trend": trend,
                            "date": obs[0].get("date"),
                        }
                    else:
                        result[key] = None
                else:
                    logger.warning("market_context: FRED %s returned HTTP %d", series_id, r.status_code)
                    result[key] = None
            except Exception as exc:
                logger.warning("market_context: FRED %s failed: %s", key, exc)
                result[key] = None

    # ── Macro score ───────────────────────────────────────────────
    macro_score = 50.0

    fed = result.get("fed_rate") or {}
    fed_val = fed.get("value")
    if fed_val is not None:
        if fed_val < 2.0:
            macro_score += 20
        elif fed_val < 4.0:
            macro_score += 5
        elif fed_val > 5.0:
            macro_score -= 15
        if fed.get("trend") == "baisse":
            macro_score += 10
        elif fed.get("trend") == "hausse":
            macro_score -= 10

    credit = result.get("credit_spread") or {}
    spread = credit.get("value")
    if spread is not None:
        if spread < 3.0:
            macro_score += 10
        elif spread > 6.0:
            macro_score -= 15

    macro_score = max(0.0, min(100.0, macro_score))
    result["macro_score"] = round(macro_score, 1)
    result["macro_regime"] = (
        "FAVORABLE" if macro_score >= 65
        else "NEUTRE" if macro_score >= 40
        else "DÉFAVORABLE"
    )

    cache_set(_CTX_NS, "fred_macro", result, ttl=_CTX_TTL)
    logger.info(
        "market_context: FRED macro fetched — score=%.1f regime=%s",
        macro_score, result["macro_regime"],
    )
    return result


def _neutral_macro() -> dict:
    return {
        "fed_rate": None,
        "cpi_yoy": None,
        "gdp_growth": None,
        "unemployment": None,
        "credit_spread": None,
        "macro_score": 50.0,
        "macro_regime": "NEUTRE",
    }


# ── Sector context ────────────────────────────────────────────────

def _fetch_sector_blocking(symbol: str, sector: str) -> dict:
    """Blocking yfinance calls — run in executor."""
    proxy = SECTOR_PROXIES.get(sector, _DEFAULT_PROXY)
    result: dict = {
        "sector": sector,
        "etf_proxy": proxy["etf"],
        "commodity": proxy.get("commodity"),
        "commodity_name": proxy.get("commodity_name"),
    }

    # ── ETF sectoriel (3 mois) ────────────────────────────────────
    try:
        hist_etf = yf.Ticker(proxy["etf"]).history(period="3mo")
        if not hist_etf.empty:
            closes = hist_etf["Close"]
            etf_start = float(closes.iloc[0])
            etf_end = float(closes.iloc[-1])
            etf_perf = (etf_end - etf_start) / etf_start * 100
            ma20 = float(closes.rolling(20).mean().iloc[-1])
            ma50 = float(closes.rolling(50).mean().iloc[-1])
            result["etf_perf_90d"] = round(etf_perf, 2)
            result["etf_trend"] = "HAUSSIER" if ma20 > ma50 else "BAISSIER"
            result["etf_price"] = round(etf_end, 2)
    except Exception as exc:
        logger.warning("market_context: ETF %s failed: %s", proxy["etf"], exc)

    # ── Commodity si applicable ───────────────────────────────────
    if proxy.get("commodity"):
        try:
            hist_comm = yf.Ticker(proxy["commodity"]).history(period="3mo")
            if not hist_comm.empty:
                comm_start = float(hist_comm["Close"].iloc[0])
                comm_end = float(hist_comm["Close"].iloc[-1])
                comm_perf = (comm_end - comm_start) / comm_start * 100
                result["commodity_price"] = round(comm_end, 2)
                result["commodity_perf_90d"] = round(comm_perf, 2)
                result["commodity_trend"] = "HAUSSIER" if comm_perf > 0 else "BAISSIER"
        except Exception as exc:
            logger.warning("market_context: commodity %s failed: %s", proxy["commodity"], exc)

    # ── Force relative vs ETF sectoriel ──────────────────────────
    try:
        hist_stock = yf.Ticker(symbol).history(period="3mo")
        if not hist_stock.empty and "etf_perf_90d" in result:
            stock_start = float(hist_stock["Close"].iloc[0])
            stock_end = float(hist_stock["Close"].iloc[-1])
            stock_perf = (stock_end - stock_start) / stock_start * 100
            rs = stock_perf - result["etf_perf_90d"]
            result["stock_perf_90d"] = round(stock_perf, 2)
            result["relative_strength"] = round(rs, 2)
            result["relative_signal"] = (
                "SURPERFORME" if rs > 3
                else "EN LIGNE" if rs > -3
                else "SOUS-PERFORME"
            )
    except Exception as exc:
        logger.warning("market_context: relative strength %s failed: %s", symbol, exc)

    # ── Score sectoriel ───────────────────────────────────────────
    sector_score = 50.0
    if result.get("etf_trend") == "HAUSSIER":
        sector_score += 20
    elif result.get("etf_trend") == "BAISSIER":
        sector_score -= 10

    rs_signal = result.get("relative_signal")
    if rs_signal == "SURPERFORME":
        sector_score += 20
    elif rs_signal == "SOUS-PERFORME":
        sector_score -= 15

    comm_trend = result.get("commodity_trend")
    if comm_trend == "HAUSSIER":
        sector_score += 10
    elif comm_trend == "BAISSIER":
        sector_score -= 10

    sector_score = max(0.0, min(100.0, sector_score))
    result["sector_score"] = round(sector_score, 1)
    result["sector_signal"] = (
        "FAVORABLE" if sector_score >= 65
        else "NEUTRE" if sector_score >= 40
        else "DÉFAVORABLE"
    )
    return result


async def fetch_sector_context(symbol: str, sector: str) -> dict:
    """
    Returns sector ETF performance, commodity trend, and stock relative strength.
    Cache TTL 1h per sector.
    """
    cache_key = f"sector_{sector.replace(' ', '_')}"
    cached = cache_get(_CTX_NS, cache_key)
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _fetch_sector_blocking, symbol, sector)

    cache_set(_CTX_NS, cache_key, result, ttl=_CTX_TTL)
    logger.info(
        "market_context: sector=%s etf_trend=%s relative=%s score=%.1f",
        sector,
        result.get("etf_trend", "N/A"),
        result.get("relative_signal", "N/A"),
        result.get("sector_score", 50),
    )
    return result


# ── Entry point ───────────────────────────────────────────────────

async def fetch_market_context(symbol: str, sector: str) -> dict:
    """
    Main entry point — aggregates macro (FRED) + sector (YFinance) context.
    Called from plan.py or any scoring module.
    """
    macro, sector_ctx = await asyncio.gather(
        fetch_fred_macro(),
        fetch_sector_context(symbol, sector),
        return_exceptions=True,
    )

    if isinstance(macro, Exception):
        logger.warning("market_context/%s: macro failed: %s", symbol, macro)
        macro = _neutral_macro()
    if isinstance(sector_ctx, Exception):
        logger.warning("market_context/%s: sector failed: %s", symbol, sector_ctx)
        sector_ctx = {"sector_score": 50.0, "sector_signal": "NEUTRE"}

    context_score = round(
        macro.get("macro_score", 50.0) * 0.40
        + sector_ctx.get("sector_score", 50.0) * 0.60,
        1,
    )

    return {
        "macro": macro,
        "sector": sector_ctx,
        "context_score": context_score,
        "context_signal": (
            "FAVORABLE" if context_score >= 65
            else "NEUTRE" if context_score >= 40
            else "DÉFAVORABLE"
        ),
    }
