"""
Lightweight world-indices service — replaces OpenBB macro endpoints.

Uses yfinance to fetch index data for S&P 500, Nasdaq, CAC 40, DAX, Nikkei.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timezone, timedelta
from typing import Any, Optional

import yfinance as yf

from app.core.logging import logger

_executor = ThreadPoolExecutor(max_workers=3)

# ── Index registry ────────────────────────────────────────────────
INDICES = {
    "^GSPC":  {"name": "S&P 500",  "exchange": "NYSE",    "tz": "America/New_York",  "open": time(9, 30), "close": time(16, 0)},
    "^NDX":   {"name": "Nasdaq 100", "exchange": "NASDAQ", "tz": "America/New_York",  "open": time(9, 30), "close": time(16, 0)},
    "^FCHI":  {"name": "CAC 40",   "exchange": "Euronext", "tz": "Europe/Paris",      "open": time(9, 0),  "close": time(17, 30)},
    "^GDAXI": {"name": "DAX",      "exchange": "XETRA",   "tz": "Europe/Berlin",     "open": time(9, 0),  "close": time(17, 30)},
    "^N225":  {"name": "Nikkei 225", "exchange": "TSE",    "tz": "Asia/Tokyo",        "open": time(9, 0),  "close": time(15, 0)},
}


def _is_market_open(tz_name: str, open_t: time, close_t: time) -> bool:
    """Check if a market is currently open (weekday + within trading hours)."""
    try:
        from zoneinfo import ZoneInfo
    except ImportError:
        from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

    now = datetime.now(ZoneInfo(tz_name))
    # Weekends are closed (Monday=0 .. Sunday=6)
    if now.weekday() >= 5:
        return False
    current = now.time()
    return open_t <= current <= close_t


def _fetch_single_index(symbol: str) -> dict[str, Any]:
    """Blocking: fetch one index's current price + previous close."""
    meta = INDICES[symbol]
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info
        price = float(fi["lastPrice"])
        prev_close = float(fi["previousClose"])
        change = round(price - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

        is_open = _is_market_open(meta["tz"], meta["open"], meta["close"])

        return {
            "symbol": symbol,
            "name": meta["name"],
            "exchange": meta["exchange"],
            "price": round(price, 2),
            "previous_close": round(prev_close, 2),
            "change": change,
            "change_pct": change_pct,
            "is_open": is_open,
        }
    except Exception as exc:
        logger.warning("Failed to fetch index %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "name": meta["name"],
            "exchange": meta["exchange"],
            "price": None,
            "previous_close": None,
            "change": None,
            "change_pct": None,
            "is_open": False,
            "error": str(exc),
        }


async def get_world_indices() -> list[dict[str, Any]]:
    """Fetch all world indices in parallel."""
    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(_executor, _fetch_single_index, sym)
        for sym in INDICES
    ]
    results = await asyncio.gather(*tasks)
    return list(results)


# ── Base-100 comparison (24h) ─────────────────────────────────────

def _fetch_comparison_series(symbol: str) -> Optional[dict[str, Any]]:
    """
    Fetch intraday data for the last 24h, normalise to base 100.

    Uses 15m intervals over the last 2 trading days to ensure coverage.
    """
    meta = INDICES[symbol]
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2d", interval="15m")
        if hist is None or hist.empty:
            return None

        close = hist["Close"].dropna()
        if close.empty:
            return None

        base = float(close.iloc[0])
        if base == 0:
            return None

        points = []
        for ts, val in close.items():
            normalised = round((float(val) / base) * 100, 4)
            points.append({
                "timestamp": ts.isoformat(),
                "value": normalised,
            })

        return {
            "symbol": symbol,
            "name": meta["name"],
            "base_price": round(base, 2),
            "latest_price": round(float(close.iloc[-1]), 2),
            "performance_pct": round(((float(close.iloc[-1]) / base) - 1) * 100, 2),
            "points": points,
        }
    except Exception as exc:
        logger.warning("Comparison fetch failed for %s: %s", symbol, exc)
        return None


async def get_comparison_data() -> list[dict[str, Any]]:
    """Fetch base-100 normalised series for all indices (parallel)."""
    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(_executor, _fetch_comparison_series, sym)
        for sym in INDICES
    ]
    results = await asyncio.gather(*tasks)
    return [r for r in results if r is not None]
