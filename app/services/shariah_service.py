"""
Lightweight yfinance-powered balance sheet fetcher for Shariah audit.

Extracts total_assets and total_debt from yfinance's quarterly or annual
balance sheet. Also fetches the adjusted close price for accurate market
cap calculations.

No heavy dependencies (no OpenBB). Build stays under 60s.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

import pandas as pd
import yfinance as yf

from app.core.logging import logger

_executor = ThreadPoolExecutor(max_workers=2)

_EMPTY: dict = {
    "total_assets": None,
    "total_debt": None,
    "adjusted_close": None,
    "source": "yfinance",
}


def _safe(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _fetch_balance_yfinance(symbol: str) -> dict:
    """
    Blocking call — fetches the latest balance sheet via yfinance.

    Tries quarterly first (more recent), falls back to annual.
    Returns {"total_assets": float|None, "total_debt": float|None,
             "adjusted_close": float|None, "source": "yfinance"}.
    """
    try:
        ticker = yf.Ticker(symbol)
    except Exception as exc:
        logger.warning("yfinance Ticker creation failed for %s: %s", symbol, exc)
        return _EMPTY.copy()

    # ── Balance sheet: quarterly first, then annual ──────────────
    bs = None
    for attr in ("quarterly_balance_sheet", "balance_sheet"):
        try:
            candidate = getattr(ticker, attr, None)
            if candidate is not None and not candidate.empty:
                bs = candidate
                logger.info("Using %s for %s (%d periods)", attr, symbol, bs.shape[1])
                break
        except Exception as exc:
            logger.debug("Failed to read %s for %s: %s", attr, symbol, exc)

    total_assets: Optional[float] = None
    total_debt: Optional[float] = None

    if bs is not None and not bs.empty:
        latest = bs.iloc[:, 0]  # most recent period

        # Total Assets
        total_assets = _safe(latest.get("Total Assets"))

        # Total Debt: direct field first, then sum of components
        total_debt = _safe(latest.get("Total Debt"))
        if total_debt is None:
            short = _safe(latest.get("Current Debt")) or _safe(latest.get("Short Term Debt")) or 0.0
            long_ = (
                _safe(latest.get("Long Term Debt"))
                or _safe(latest.get("Long Term Debt And Capital Lease Obligation"))
                or 0.0
            )
            if short > 0 or long_ > 0:
                total_debt = short + long_

        logger.info(
            "Balance sheet for %s: total_assets=%s, total_debt=%s",
            symbol, total_assets, total_debt,
        )
    else:
        logger.warning("No balance sheet data from yfinance for %s", symbol)

    # ── Adjusted close price (fixes the NVO $38.58 bug) ─────────
    adjusted_close: Optional[float] = None
    try:
        hist = ticker.history(period="5d")
        if hist is not None and not hist.empty:
            adjusted_close = float(hist["Close"].iloc[-1])
            logger.info("Adjusted close for %s: %.2f", symbol, adjusted_close)
    except Exception as exc:
        logger.debug("Failed to fetch adjusted close for %s: %s", symbol, exc)

    return {
        "total_assets": total_assets,
        "total_debt": total_debt,
        "adjusted_close": adjusted_close,
        "source": "yfinance",
    }


async def get_audit_data(symbol: str) -> dict:
    """
    Async wrapper — returns balance sheet + adjusted close from yfinance.
    Never raises; returns empty results on failure.
    """
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(_executor, _fetch_balance_yfinance, symbol)
    except Exception as exc:
        logger.warning("get_audit_data failed for %s: %s", symbol, exc)
        return _EMPTY.copy()
