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


# ── 24h Follow-the-Sun comparison (Base 0 per session open) ───────

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

# Short labels for the LineChart
_CHART_KEYS: dict[str, str] = {
    "^GSPC":  "US500",
    "^NDX":   "NDX100",
    "^FCHI":  "FR40",
    "^GDAXI": "DE40",
    "^N225":  "JP225",
}


def _fetch_intraday_24h(symbol: str) -> Optional[list[tuple[str, float]]]:
    """
    Fetch the last 2 trading days at 15-min intervals, trim to the most
    recent 24 UTC hours, and normalise each index to 0% at its own
    session-open price.

    Returns list of (UTC "HH:MM", pct_change) sorted chronologically.
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

        # Convert index to UTC for a unified axis
        utc = ZoneInfo("UTC")
        local_tz = ZoneInfo(meta["tz"])
        open_time = meta["open"]

        # Find session-open price: the first bar of the most recent
        # trading session in the index's local timezone.
        session_open_price: Optional[float] = None
        for ts, val in close.items():
            local_dt = ts.astimezone(local_tz)
            if local_dt.time() >= open_time:
                session_open_price = float(val)
                break

        if session_open_price is None or session_open_price == 0:
            # Fallback: use the very first data point
            session_open_price = float(close.iloc[0])
            if session_open_price == 0:
                return None

        # Trim to last 24 UTC hours
        now_utc = datetime.now(utc)
        cutoff = now_utc - timedelta(hours=24)

        points: list[tuple[str, float]] = []
        for ts, val in close.items():
            ts_utc = ts.astimezone(utc)
            if ts_utc < cutoff:
                continue
            pct = round(((float(val) / session_open_price) - 1) * 100, 2)
            time_label = ts_utc.strftime("%H:%M")
            points.append((time_label, pct))

        return points if points else None
    except Exception as exc:
        logger.warning("Intraday 24h fetch failed for %s: %s", symbol, exc)
        return None


async def get_comparison_data() -> list[dict[str, Any]]:
    """
    24h Follow-the-Sun comparison for LineChart.

    Returns a flat array sorted by UTC time:
      [{time: "00:15", JP225: 0.3}, {time: "09:00", FR40: 0, DE40: 0}, ...]

    - Each index's 0% = its own session-open price.
    - Axis covers 24 UTC hours so Asia → Europe → USA sessions are visible.
    - null when an index has no data at that timestamp.
    """
    loop = asyncio.get_running_loop()
    tasks = {
        sym: loop.run_in_executor(_executor, _fetch_intraday_24h, sym)
        for sym in INDICES
    }

    raw: dict[str, Optional[list[tuple[str, float]]]] = {}
    for sym, task in tasks.items():
        raw[sym] = await task

    # Merge all indices onto one UTC time axis
    time_set: dict[str, dict[str, float]] = {}
    for sym, points in raw.items():
        if points is None:
            continue
        key = _CHART_KEYS[sym]
        for t, pct in points:
            if t not in time_set:
                time_set[t] = {}
            time_set[t][key] = pct

    if not time_set:
        return []

    all_keys = sorted({k for row in time_set.values() for k in row})
    result: list[dict[str, Any]] = []
    for t in sorted(time_set.keys()):
        row: dict[str, Any] = {"time": t}
        for key in all_keys:
            row[key] = time_set[t].get(key)
        result.append(row)

    return result


# ── Commodities ───────────────────────────────────────────────────
# CME/NYMEX futures trade nearly 24h (Sun 18:00 – Fri 17:00 ET).

COMMODITIES = {
    "GC=F":  {"name": "Gold",        "unit": "USD/oz"},
    "BZ=F":  {"name": "Brent Oil",   "unit": "USD/bbl"},
    "NG=F":  {"name": "Natural Gas",  "unit": "USD/MMBtu"},
    "SI=F":  {"name": "Silver",      "unit": "USD/oz"},
}

_COMMODITY_KEYS: dict[str, str] = {
    "GC=F": "GOLD",
    "BZ=F": "BRENT",
    "NG=F": "NATGAS",
    "SI=F": "SILVER",
}


def _fetch_single_commodity(symbol: str) -> dict[str, Any]:
    """Blocking: fetch one commodity's price + daily change."""
    meta = COMMODITIES[symbol]
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info
        price = float(fi["lastPrice"])
        prev_close = float(fi["previousClose"])
        change = round(price - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

        # CME futures: open weekdays, closed Sat and most of Sun
        now_et = datetime.now(ZoneInfo("America/New_York"))
        wd = now_et.weekday()
        is_open = wd < 5 or (wd == 6 and now_et.hour >= 18)

        return {
            "symbol": symbol,
            "name": meta["name"],
            "unit": meta["unit"],
            "price": round(price, 2),
            "previous_close": round(prev_close, 2),
            "change": change,
            "change_pct": change_pct,
            "is_open": is_open,
        }
    except Exception as exc:
        logger.warning("Failed to fetch commodity %s: %s", symbol, exc)
        return {
            "symbol": symbol,
            "name": meta["name"],
            "unit": meta["unit"],
            "price": None,
            "previous_close": None,
            "change": None,
            "change_pct": None,
            "is_open": False,
            "error": str(exc),
        }


async def get_commodities_data() -> list[dict[str, Any]]:
    """Fetch all commodity snapshots in parallel."""
    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(_executor, _fetch_single_commodity, sym)
        for sym in COMMODITIES
    ]
    results = await asyncio.gather(*tasks)
    return list(results)


# ── Commodities 24h comparison (same format as indices) ───────────

def _fetch_commodity_intraday(symbol: str) -> Optional[list[tuple[str, float]]]:
    """Fetch 24h intraday for a commodity, normalised to 0% at first point."""
    try:
        ticker = yf.Ticker(symbol)
        hist = ticker.history(period="2d", interval="15m")
        if hist is None or hist.empty:
            return None

        close = hist["Close"].dropna()
        if close.empty:
            return None

        utc = ZoneInfo("UTC")
        now_utc = datetime.now(utc)
        cutoff = now_utc - timedelta(hours=24)

        # Base = first price within the 24h window
        base: Optional[float] = None
        points: list[tuple[str, float]] = []
        for ts, val in close.items():
            ts_utc = ts.astimezone(utc)
            if ts_utc < cutoff:
                continue
            v = float(val)
            if base is None:
                base = v
            if base == 0:
                return None
            pct = round(((v / base) - 1) * 100, 2)
            points.append((ts_utc.strftime("%H:%M"), pct))

        return points if points else None
    except Exception as exc:
        logger.warning("Commodity intraday fetch failed for %s: %s", symbol, exc)
        return None


async def get_commodities_comparison() -> list[dict[str, Any]]:
    """
    24h commodity comparison for LineChart — same flat format as indices.

    [{time: "00:15", GOLD: 0.1, BRENT: -0.3, ...}, ...]
    """
    loop = asyncio.get_running_loop()
    tasks = {
        sym: loop.run_in_executor(_executor, _fetch_commodity_intraday, sym)
        for sym in COMMODITIES
    }

    raw: dict[str, Optional[list[tuple[str, float]]]] = {}
    for sym, task in tasks.items():
        raw[sym] = await task

    time_set: dict[str, dict[str, float]] = {}
    for sym, points in raw.items():
        if points is None:
            continue
        key = _COMMODITY_KEYS[sym]
        for t, pct in points:
            if t not in time_set:
                time_set[t] = {}
            time_set[t][key] = pct

    if not time_set:
        return []

    all_keys = sorted({k for row in time_set.values() for k in row})
    result: list[dict[str, Any]] = []
    for t in sorted(time_set.keys()):
        row: dict[str, Any] = {"time": t}
        for key in all_keys:
            row[key] = time_set[t].get(key)
        result.append(row)

    return result
