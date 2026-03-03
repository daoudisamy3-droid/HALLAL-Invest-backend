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
