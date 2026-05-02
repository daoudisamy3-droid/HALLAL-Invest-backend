import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Optional

import yfinance as yf
import pandas as pd

from app.core.logging import logger

_executor = ThreadPoolExecutor(max_workers=4)


def _fetch_ticker_info(symbol: str) -> dict[str, Any]:
    """Blocking call — run in executor."""
    ticker = yf.Ticker(symbol)
    info = ticker.info

    if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
        raise ValueError(f"No data found for symbol: {symbol}")
    return info


def _fetch_history(symbol: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    """Blocking call — run in executor."""
    ticker = yf.Ticker(symbol)
    df = ticker.history(period=period, interval=interval)
    if df.empty:
        raise ValueError(f"No historical data for {symbol} ({period}/{interval})")
    return df


def _fetch_fast_price(symbol: str) -> dict[str, float]:
    """Minimal blocking call — only fetch price data via fast_info."""
    ticker = yf.Ticker(symbol)
    fi = ticker.fast_info
    price = float(fi["lastPrice"])
    prev = float(fi["previousClose"])
    change = round(price - prev, 4)
    change_pct = round((change / prev) * 100, 2) if prev else 0.0
    return {"current_price": round(price, 2), "change": round(change, 2), "change_pct": change_pct}


async def get_ticker_info(symbol: str) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    logger.info("Fetching ticker info for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_ticker_info, symbol)


async def get_history(symbol: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    loop = asyncio.get_running_loop()
    logger.info("Fetching %s history for %s", period, symbol)
    return await loop.run_in_executor(_executor, _fetch_history, symbol, period, interval)


async def get_fast_price(symbol: str) -> dict[str, float]:
    loop = asyncio.get_running_loop()
    logger.info("Fetching fast price for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_fast_price, symbol)


def _fetch_balance_sheet(symbol: str) -> Optional[dict]:
    """Fetch latest annual balance sheet from yfinance as a flat dict."""
    ticker = yf.Ticker(symbol)
    bs = ticker.balance_sheet
    if bs is None or bs.empty:
        return None
    # bs columns are dates, rows are line items. Take the latest column.
    latest = bs.iloc[:, 0]
    result = {row: val for row, val in latest.items() if pd.notna(val)}
    logger.info("yfinance balance sheet for %s: %d items", symbol, len(result))
    return result


def _fetch_income_stmt(symbol: str) -> Optional[dict]:
    """Fetch latest annual income statement from yfinance as a flat dict."""
    ticker = yf.Ticker(symbol)
    inc = ticker.income_stmt
    if inc is None or inc.empty:
        return None
    latest = inc.iloc[:, 0]
    result = {row: val for row, val in latest.items() if pd.notna(val)}
    logger.info("yfinance income stmt for %s: %d items", symbol, len(result))
    return result


def _fetch_cashflow(symbol: str) -> Optional[dict]:
    """Fetch latest annual cashflow statement from yfinance as a flat dict."""
    ticker = yf.Ticker(symbol)
    cf = ticker.cashflow
    if cf is None or cf.empty:
        return None
    latest = cf.iloc[:, 0]
    result = {row: val for row, val in latest.items() if pd.notna(val)}
    logger.info("yfinance cashflow for %s: %d items", symbol, len(result))
    return result


async def get_balance_sheet(symbol: str) -> Optional[dict]:
    loop = asyncio.get_running_loop()
    logger.info("Fetching yfinance balance sheet for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_balance_sheet, symbol)


async def get_cashflow(symbol: str) -> Optional[dict]:
    loop = asyncio.get_running_loop()
    logger.info("Fetching yfinance cashflow for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_cashflow, symbol)


async def get_income_stmt(symbol: str) -> Optional[dict]:
    loop = asyncio.get_running_loop()
    logger.info("Fetching yfinance income statement for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_income_stmt, symbol)


def _fetch_interest_income_from_funds_data(symbol: str) -> Optional[float]:
    """Try to extract interest income from yfinance funds_data (ETFs/funds)."""
    try:
        ticker = yf.Ticker(symbol)
        if hasattr(ticker, "funds_data") and ticker.funds_data is not None:
            fd = ticker.funds_data
            # funds_data may expose asset_classes or other breakdowns
            if hasattr(fd, "asset_classes"):
                ac = fd.asset_classes
                if isinstance(ac, dict):
                    val = ac.get("bond", {}).get("interest", None)
                    if val is not None:
                        return abs(float(val))
            # Some funds expose top holdings with income info
            logger.info("yfinance funds_data for %s: no interest income field found", symbol)
        return None
    except Exception as exc:
        logger.debug("yfinance funds_data lookup failed for %s: %s", symbol, exc)
        return None


async def get_interest_income_fallback(symbol: str) -> Optional[float]:
    """
    Dedicated fallback to extract interest income from yfinance.
    Tries income_stmt first, then funds_data.
    """
    loop = asyncio.get_running_loop()

    # Attempt 1: standard income statement
    stmt = await loop.run_in_executor(_executor, _fetch_income_stmt, symbol)
    if stmt:
        for key in ("Interest Income", "Interest Expense", "Interest Income Non Operating",
                     "Interest Expense Non Operating"):
            val = stmt.get(key)
            if val is not None:
                try:
                    result = abs(float(val))
                    if result > 0:
                        logger.info("yfinance interest income fallback for %s: %s = %.0f", symbol, key, result)
                        return result
                except (ValueError, TypeError):
                    continue

    # Attempt 2: funds_data (for ETFs / funds)
    result = await loop.run_in_executor(_executor, _fetch_interest_income_from_funds_data, symbol)
    if result is not None and result > 0:
        logger.info("yfinance funds_data interest income for %s: %.0f", symbol, result)
        return result

    logger.info("yfinance interest income fallback: nothing found for %s", symbol)
    return None


def _fetch_analyst_sentiment(symbol: str) -> dict[str, Any]:
    """
    Fetch analyst sentiment data from a single Ticker object to minimize
    Yahoo requests. Returns info keys + recommendations breakdown.
    """
    ticker = yf.Ticker(symbol)
    info = ticker.info

    if not info or (info.get("regularMarketPrice") is None and info.get("currentPrice") is None):
        raise ValueError(f"No data found for symbol: {symbol}")

    # Extract consensus fields from info
    result: dict[str, Any] = {
        "recommendation_mean": info.get("recommendationMean"),
        "recommendation_key": info.get("recommendationKey"),
        "target_mean_price": info.get("targetMeanPrice"),
        "number_of_analyst_opinions": info.get("numberOfAnalystOpinions"),
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "market_cap": info.get("marketCap"),
    }

    # Fetch recommendations breakdown from the same Ticker object
    breakdown: dict[str, int] = {
        "strong_buy": 0, "buy": 0, "hold": 0, "sell": 0, "strong_sell": 0,
    }
    try:
        recs = ticker.recommendations
        if recs is not None and not recs.empty:
            # yfinance returns a DataFrame; take the most recent row
            latest = recs.iloc[-1]
            for col in latest.index:
                col_lower = col.lower().replace(" ", "_")
                if col_lower in breakdown:
                    breakdown[col_lower] = int(latest[col])
            # Some versions use 'strongBuy'/'strongSell' column names
            if "strongBuy" in latest.index:
                breakdown["strong_buy"] = int(latest["strongBuy"])
            if "strongSell" in latest.index:
                breakdown["strong_sell"] = int(latest["strongSell"])
    except Exception as exc:
        logger.debug("yfinance recommendations fetch failed for %s: %s", symbol, exc)

    result["breakdown"] = breakdown
    logger.info(
        "yfinance analyst sentiment for %s: mean=%.2f key=%s opinions=%s",
        symbol,
        result["recommendation_mean"] or 0,
        result["recommendation_key"],
        result["number_of_analyst_opinions"],
    )
    return result


async def get_analyst_sentiment(symbol: str) -> dict[str, Any]:
    """Fetch analyst sentiment — bundled info + recommendations in one call."""
    loop = asyncio.get_running_loop()
    logger.info("Fetching analyst sentiment for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_analyst_sentiment, symbol)


def _fetch_calendar(symbol: str) -> dict[str, Any]:
    """
    Fetch earnings calendar + dividend info + last 4 quarters of earnings history.

    Returns a plain dict (no DataFrames) safe for cache serialisation.
    """
    from datetime import date, timedelta

    ticker = yf.Ticker(symbol)
    info = ticker.info or {}

    if not info or (
        info.get("regularMarketPrice") is None and info.get("currentPrice") is None
    ):
        raise ValueError(f"No data found for symbol: {symbol}")

    # ── Raw calendar ─────────────────────────────────────────────
    try:
        raw_cal = ticker.calendar or {}
        # yfinance may return a DataFrame in some versions
        if hasattr(raw_cal, "to_dict"):
            raw_cal = {}
    except Exception:
        raw_cal = {}

    # ── Earnings next dates ───────────────────────────────────────
    earnings_raw = raw_cal.get("Earnings Date", [])
    if not isinstance(earnings_raw, list):
        earnings_raw = [earnings_raw] if earnings_raw is not None else []

    def _to_date_str(ts) -> str | None:
        try:
            if hasattr(ts, "date"):
                return ts.date().isoformat()
            return str(ts)[:10]
        except Exception:
            return None

    earnings_dates_list = [_to_date_str(ts) for ts in earnings_raw if ts is not None]
    earnings_dates_list = [d for d in earnings_dates_list if d]

    # ── Earnings history (4 last quarters) ───────────────────────
    # ticker.earnings_history contains past actuals (epsEstimate, epsActual,
    # epsDifference, surprisePercent) — unlike earnings_dates which is future.
    history: list[dict] = []
    try:
        hist = ticker.earnings_history
        if hist is not None and not hist.empty:
            past = hist[hist.index < pd.Timestamp.now()]
            past = past.tail(4).sort_index(ascending=False)
            for idx, row in past.iterrows():
                eps_est = row.get("epsEstimate")
                eps_act = row.get("epsActual")
                surprise = row.get("surprisePercent")

                def _safe_float(v):
                    try:
                        f = float(v)
                        return None if pd.isna(f) else f
                    except (TypeError, ValueError):
                        return None

                eps_est_f = _safe_float(eps_est)
                eps_act_f = _safe_float(eps_act)
                surprise_f = _safe_float(surprise)

                beat: bool | None = None
                if eps_est_f is not None and eps_act_f is not None:
                    beat = eps_act_f > eps_est_f

                history.append({
                    "date": idx.strftime("%Y-%m-%d"),
                    "eps_estimate": round(eps_est_f, 4) if eps_est_f is not None else None,
                    "eps_reported": round(eps_act_f, 4) if eps_act_f is not None else None,
                    "surprise_pct": round(surprise_f, 2) if surprise_f is not None else None,
                    "beat": beat,
                })
    except Exception as exc:
        logger.debug("yfinance earnings_history failed for %s: %s", symbol, exc)

    # ── Dividend info ─────────────────────────────────────────────
    def _unix_to_iso(ts) -> str | None:
        if ts is None:
            return None
        try:
            return date.fromtimestamp(int(ts)).isoformat()
        except Exception:
            return None

    def _prev_biz_day(d: date) -> date:
        d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d = d - timedelta(days=1)
        return d

    ex_div_iso = _unix_to_iso(info.get("exDividendDate"))
    div_pay_iso = _unix_to_iso(info.get("dividendDate"))
    div_rate = info.get("dividendRate")
    div_yield = info.get("dividendYield")

    entry_deadline_iso: str | None = None
    if ex_div_iso:
        try:
            ex_date_obj = date.fromisoformat(ex_div_iso)
            entry_deadline_iso = _prev_biz_day(ex_date_obj).isoformat()
        except Exception:
            pass

    has_dividend = bool(div_rate and float(div_rate) > 0)
    is_adr = "ADR" in (info.get("longName") or "") or info.get("quoteType") == "ADR"

    logger.info(
        "yfinance calendar for %s: earnings=%s div_rate=%s is_adr=%s history_len=%d",
        symbol, earnings_dates_list, div_rate, is_adr, len(history),
    )

    return {
        "earnings_dates_list": earnings_dates_list,
        "history": history,
        "ex_div_iso": ex_div_iso,
        "div_pay_iso": div_pay_iso,
        "div_rate": float(div_rate) if div_rate is not None else None,
        "div_yield": float(div_yield) if div_yield is not None else None,
        "entry_deadline_iso": entry_deadline_iso,
        "has_dividend": has_dividend,
        "is_adr": is_adr,
    }


async def get_calendar(symbol: str) -> dict[str, Any]:
    """Fetch earnings calendar + dividend + history for a symbol."""
    loop = asyncio.get_running_loop()
    logger.info("Fetching calendar data for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_calendar, symbol)


