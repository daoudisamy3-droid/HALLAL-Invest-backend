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


async def get_balance_sheet(symbol: str) -> Optional[dict]:
    loop = asyncio.get_running_loop()
    logger.info("Fetching yfinance balance sheet for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_balance_sheet, symbol)


async def get_income_stmt(symbol: str) -> Optional[dict]:
    loop = asyncio.get_running_loop()
    logger.info("Fetching yfinance income statement for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_income_stmt, symbol)
