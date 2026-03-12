"""
Lightweight world-indices & commodities service.

Uses yfinance for S&P 500, Nasdaq, CAC 40, DAX, Nikkei, Gold, Brent, NatGas, Silver, DXY.
Includes a 60-second non-blocking TTL cache and closed-market last-close persistence.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timezone, timedelta
from typing import Any, Optional

import yfinance as yf

from app.core.logging import logger

_executor = ThreadPoolExecutor(max_workers=5)

try:
    from zoneinfo import ZoneInfo
except ImportError:
    from backports.zoneinfo import ZoneInfo  # type: ignore[no-redef]

# ── Lightweight TTL cache (60s, non-blocking) ─────────────────────
_CACHE_TTL = 60  # seconds
_cache: dict[str, tuple[float, Any]] = {}


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    if (datetime.now(timezone.utc).timestamp() - ts) > _CACHE_TTL:
        return None
    return data


def _cache_set(key: str, data: Any) -> None:
    _cache[key] = (datetime.now(timezone.utc).timestamp(), data)


# ── Index registry ────────────────────────────────────────────────
INDICES = {
    "^GSPC":  {"name": "S&P 500",    "exchange": "NYSE",    "tz": "America/New_York", "open": time(9, 30), "close": time(16, 0)},
    "^NDX":   {"name": "Nasdaq 100", "exchange": "NASDAQ",  "tz": "America/New_York", "open": time(9, 30), "close": time(16, 0)},
    "^FCHI":  {"name": "CAC 40",     "exchange": "Euronext", "tz": "Europe/Paris",     "open": time(9, 0),  "close": time(17, 30)},
    "^GDAXI": {"name": "DAX",        "exchange": "XETRA",   "tz": "Europe/Berlin",    "open": time(9, 0),  "close": time(17, 30)},
    "^N225":  {"name": "Nikkei 225", "exchange": "TSE",     "tz": "Asia/Tokyo",       "open": time(9, 0),  "close": time(15, 0)},
}


def _is_market_open(tz_name: str, open_t: time, close_t: time) -> bool:
    """Check if a market is currently open (weekday + within trading hours)."""
    now = datetime.now(ZoneInfo(tz_name))
    if now.weekday() >= 5:
        return False
    current = now.time()
    return open_t <= current <= close_t


# In-memory last-known prices so closed markets still show a line
_last_known: dict[str, dict[str, Any]] = {}


def _fetch_single_index(symbol: str) -> dict[str, Any]:
    """Blocking: fetch one index's current price + previous close."""
    meta = INDICES[symbol]
    is_open = _is_market_open(meta["tz"], meta["open"], meta["close"])
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info
        price = float(fi["lastPrice"])
        prev_close = float(fi["previousClose"])
        change = round(price - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

        result = {
            "symbol": symbol,
            "name": meta["name"],
            "exchange": meta["exchange"],
            "price": round(price, 2),
            "previous_close": round(prev_close, 2),
            "change": change,
            "change_pct": change_pct,
            "is_open": is_open,
        }
        _last_known[symbol] = result
        return result
    except Exception as exc:
        logger.warning("Failed to fetch index %s: %s", symbol, exc)
        cached = _last_known.get(symbol)
        if cached:
            return {**cached, "is_open": is_open}
        return {
            "symbol": symbol,
            "name": meta["name"],
            "exchange": meta["exchange"],
            "price": None,
            "previous_close": None,
            "change": None,
            "change_pct": None,
            "is_open": is_open,
            "error": str(exc),
        }


async def get_world_indices() -> list[dict[str, Any]]:
    """Fetch all world indices in parallel (60s TTL cache)."""
    cached = _cache_get("world_indices")
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(_executor, _fetch_single_index, sym)
        for sym in INDICES
    ]
    results = list(await asyncio.gather(*tasks))
    _cache_set("world_indices", results)
    return results


# ── 24h Follow-the-Sun comparison (Base 0 per session open) ───────

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
            points.append((ts_utc.strftime("%H:%M"), pct))

        return points if points else None
    except Exception as exc:
        logger.warning("Intraday 24h fetch failed for %s: %s", symbol, exc)
        return None


def _merge_to_flat_rows(
    raw: dict[str, Optional[list[tuple[str, float]]]],
    key_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Merge per-symbol point lists into flat [{time, KEY1, KEY2, ...}]."""
    time_set: dict[str, dict[str, float]] = {}
    for sym, points in raw.items():
        if points is None:
            continue
        key = key_map[sym]
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


async def get_comparison_data() -> list[dict[str, Any]]:
    """
    24h Follow-the-Sun comparison for LineChart (60s TTL cache).

    [{time: "00:15", JP225: 0.3}, {time: "09:00", FR40: 0, DE40: 0}, ...]
    """
    cached = _cache_get("indices_comparison")
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    tasks = {
        sym: loop.run_in_executor(_executor, _fetch_intraday_24h, sym)
        for sym in INDICES
    }
    raw: dict[str, Optional[list[tuple[str, float]]]] = {}
    for sym, task in tasks.items():
        raw[sym] = await task

    result = _merge_to_flat_rows(raw, _CHART_KEYS)
    _cache_set("indices_comparison", result)
    return result


# ── Commodities + DXY ─────────────────────────────────────────────
# CME/NYMEX futures trade nearly 24h (Sun 18:00 – Fri 17:00 ET).

COMMODITIES = {
    "GC=F":     {"name": "Gold",         "unit": "USD/oz"},
    "BZ=F":     {"name": "Brent Oil",    "unit": "USD/bbl"},
    "NG=F":     {"name": "Natural Gas",  "unit": "USD/MMBtu"},
    "SI=F":     {"name": "Silver",       "unit": "USD/oz"},
    "DX-Y.NYB": {"name": "Dollar Index", "unit": "Index"},
}

_COMMODITY_KEYS: dict[str, str] = {
    "GC=F":     "GOLD",
    "BZ=F":     "BRENT",
    "NG=F":     "NATGAS",
    "SI=F":     "SILVER",
    "DX-Y.NYB": "DXY",
}

_last_known_commodity: dict[str, dict[str, Any]] = {}


def _is_cme_open() -> bool:
    """CME/NYMEX futures: Sun 18:00 ET – Fri 17:00 ET."""
    now_et = datetime.now(ZoneInfo("America/New_York"))
    wd = now_et.weekday()
    h = now_et.hour
    if wd == 5:
        return False
    if wd == 6:
        return h >= 18
    if wd == 4:
        return h < 17
    return not (h == 17)


def _fetch_single_commodity(symbol: str) -> dict[str, Any]:
    """Blocking: fetch one commodity's price + daily change."""
    meta = COMMODITIES[symbol]
    is_open = _is_cme_open()
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info
        price = float(fi["lastPrice"])
        prev_close = float(fi["previousClose"])
        change = round(price - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

        result = {
            "symbol": symbol,
            "name": meta["name"],
            "unit": meta["unit"],
            "price": round(price, 2),
            "previous_close": round(prev_close, 2),
            "change": change,
            "change_pct": change_pct,
            "is_open": is_open,
        }
        _last_known_commodity[symbol] = result
        return result
    except Exception as exc:
        logger.warning("Failed to fetch commodity %s: %s", symbol, exc)
        cached = _last_known_commodity.get(symbol)
        if cached:
            return {**cached, "is_open": is_open}
        return {
            "symbol": symbol,
            "name": meta["name"],
            "unit": meta["unit"],
            "price": None,
            "previous_close": None,
            "change": None,
            "change_pct": None,
            "is_open": is_open,
            "error": str(exc),
        }


async def get_commodities_data() -> list[dict[str, Any]]:
    """Fetch all commodity + DXY snapshots in parallel (60s TTL cache)."""
    cached = _cache_get("commodities")
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(_executor, _fetch_single_commodity, sym)
        for sym in COMMODITIES
    ]
    results = list(await asyncio.gather(*tasks))
    _cache_set("commodities", results)
    return results


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
    24h commodity + DXY comparison for LineChart (60s TTL cache).

    [{time: "00:15", GOLD: 0.1, BRENT: -0.3, DXY: 0.05, ...}, ...]
    """
    cached = _cache_get("commodities_comparison")
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    tasks = {
        sym: loop.run_in_executor(_executor, _fetch_commodity_intraday, sym)
        for sym in COMMODITIES
    }
    raw: dict[str, Optional[list[tuple[str, float]]]] = {}
    for sym, task in tasks.items():
        raw[sym] = await task

    result = _merge_to_flat_rows(raw, _COMMODITY_KEYS)
    _cache_set("commodities_comparison", result)
    return result


# ── Risk indicators (VIX + US 10Y) ───────────────────────────────

RISK_TICKERS = {
    "^VIX": {"name": "VIX (Volatility)", "unit": "pts"},
    "^TNX": {"name": "US 10Y Yield",     "unit": "%"},
}

_RISK_KEYS: dict[str, str] = {
    "^VIX": "VIX",
    "^TNX": "US10Y",
}

_last_known_risk: dict[str, dict[str, Any]] = {}


def _risk_signal(symbol: str, value: Optional[float], change_pct: Optional[float]) -> str:
    """Compute a human-readable risk signal."""
    if symbol == "^VIX":
        if value is not None and value > 25:
            return "\U0001f534 VIGILANCE"
        return "\U0001f7e2 STABLE"
    if symbol == "^TNX":
        if change_pct is not None and abs(change_pct) > 1:
            return "\u26a0\ufe0f TENSION TAUX"
        return "OK"
    return "N/A"


def _fetch_single_risk(symbol: str) -> dict[str, Any]:
    """Blocking: fetch one risk indicator's value + daily change."""
    meta = RISK_TICKERS[symbol]
    is_open = _is_market_open("America/New_York", time(9, 30), time(16, 0))
    try:
        ticker = yf.Ticker(symbol)
        fi = ticker.fast_info
        price = float(fi["lastPrice"])
        prev_close = float(fi["previousClose"])
        change = round(price - prev_close, 2)
        change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

        signal = _risk_signal(symbol, round(price, 2), change_pct)

        result = {
            "symbol": symbol,
            "name": meta["name"],
            "unit": meta["unit"],
            "value": round(price, 2),
            "previous_close": round(prev_close, 2),
            "change": change,
            "change_pct": change_pct,
            "signal": signal,
            "is_open": is_open,
        }
        _last_known_risk[symbol] = result
        return result
    except Exception as exc:
        logger.warning("Failed to fetch risk indicator %s: %s", symbol, exc)
        cached = _last_known_risk.get(symbol)
        if cached:
            return {**cached, "is_open": is_open}
        return {
            "symbol": symbol,
            "name": meta["name"],
            "unit": meta["unit"],
            "value": None,
            "previous_close": None,
            "change": None,
            "change_pct": None,
            "signal": "N/A",
            "is_open": is_open,
            "error": str(exc),
        }


async def get_risk_data() -> list[dict[str, Any]]:
    """Fetch VIX + US10Y snapshots in parallel (60s TTL cache)."""
    cached = _cache_get("risk")
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    tasks = [
        loop.run_in_executor(_executor, _fetch_single_risk, sym)
        for sym in RISK_TICKERS
    ]
    results = list(await asyncio.gather(*tasks))
    _cache_set("risk", results)
    return results


# ── Risk 24h comparison ───────────────────────────────────────────

def _fetch_risk_intraday(symbol: str) -> Optional[list[tuple[str, float]]]:
    """Fetch 24h intraday for a risk indicator, normalised to 0% at first point."""
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
        logger.warning("Risk intraday fetch failed for %s: %s", symbol, exc)
        return None


async def get_risk_comparison() -> list[dict[str, Any]]:
    """
    24h risk comparison for LineChart (60s TTL cache).

    [{time: "09:45", VIX: 1.2, US10Y: -0.3}, ...]
    """
    cached = _cache_get("risk_comparison")
    if cached is not None:
        return cached

    loop = asyncio.get_running_loop()
    tasks = {
        sym: loop.run_in_executor(_executor, _fetch_risk_intraday, sym)
        for sym in RISK_TICKERS
    }
    raw: dict[str, Optional[list[tuple[str, float]]]] = {}
    for sym, task in tasks.items():
        raw[sym] = await task

    result = _merge_to_flat_rows(raw, _RISK_KEYS)
    _cache_set("risk_comparison", result)
    return result
