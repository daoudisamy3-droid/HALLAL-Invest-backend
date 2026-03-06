import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import yfinance as yf
import pandas as pd

from app.core.logging import logger

_executor = ThreadPoolExecutor(max_workers=4)


def _fetch_ticker_info(symbol: str) -> dict[str, Any]:
    """Blocking call — run in executor."""
    # On laisse yfinance gérer son moteur interne (curl_cffi)
    ticker = yf.Ticker(symbol)
    info = ticker.info

    # Vérification hybride : certains tickers n'ont que l'un ou l'autre
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


def _fetch_financials(symbol: str) -> dict[str, Any]:
    """Fetch income statement, balance sheet, and cash flow."""
    ticker = yf.Ticker(symbol)
    return {
        "income_stmt": ticker.income_stmt,
        "balance_sheet": ticker.balance_sheet,
        "cashflow": ticker.cashflow,
    }


def _fetch_fast_price(symbol: str) -> dict[str, float]:
    """Minimal blocking call — only fetch price data via fast_info."""
    ticker = yf.Ticker(symbol)
    fi = ticker.fast_info
    price = float(fi["lastPrice"])
    prev = float(fi["previousClose"])
    change = round(price - prev, 4)
    change_pct = round((change / prev) * 100, 2) if prev else 0.0
    return {"current_price": round(price, 2), "change": round(change, 2), "change_pct": change_pct}


async def get_fast_price(symbol: str) -> dict[str, float]:
    loop = asyncio.get_running_loop()
    logger.info("Fetching fast price for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_fast_price, symbol)


async def get_ticker_info(symbol: str) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    logger.info("Fetching ticker info for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_ticker_info, symbol)


async def get_history(symbol: str, period: str = "5y", interval: str = "1d") -> pd.DataFrame:
    loop = asyncio.get_running_loop()
    logger.info("Fetching %s history for %s", period, symbol)
    return await loop.run_in_executor(_executor, _fetch_history, symbol, period, interval)


async def get_financials(symbol: str) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    logger.info("Fetching financials for %s", symbol)
    return await loop.run_in_executor(_executor, _fetch_financials, symbol)
