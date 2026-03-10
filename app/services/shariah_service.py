"""
OpenBB-powered balance sheet fetcher for Shariah audit.

Uses `obb.equity.fundamental.balance` with the yfinance provider
to extract total_assets and total_debt for the AAOIFI debt/assets ratio.

Gracefully degrades: if OpenBB is not installed or fails to import,
get_audit_data() returns empty results so the backend starts without error.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

from app.core.logging import logger

_executor = ThreadPoolExecutor(max_workers=2)

# ── Safe import: backend starts even if openbb is missing ─────────
_obb = None
try:
    from openbb import obb as _obb  # noqa: F811
    logger.info("OpenBB SDK loaded successfully")
except ImportError:
    logger.warning("OpenBB SDK not available — shariah_service will return empty results")
except Exception as exc:
    logger.warning("OpenBB SDK import error: %s — shariah_service will return empty results", exc)

_EMPTY = {"total_assets": None, "total_debt": None, "source": "openbb/yfinance"}


def _fetch_balance_openbb(symbol: str) -> dict:
    """
    Blocking call — fetches the latest annual balance sheet via OpenBB SDK.
    Returns {"total_assets": float|None, "total_debt": float|None, "source": "openbb/yfinance"}.
    """
    if _obb is None:
        return _EMPTY

    try:
        result = _obb.equity.fundamental.balance(symbol, provider="yfinance")
    except Exception as exc:
        logger.warning("OpenBB balance fetch failed for %s: %s", symbol, exc)
        return _EMPTY

    # result.results is a list of balance sheet entries; take the most recent
    entries = result.results if hasattr(result, "results") else []
    if not entries:
        logger.warning("OpenBB returned no balance sheet data for %s", symbol)
        return _EMPTY

    latest = entries[0]

    # Extract total_assets
    total_assets: Optional[float] = None
    val = getattr(latest, "total_assets", None)
    if val is not None:
        try:
            total_assets = float(val)
        except (ValueError, TypeError):
            pass

    # Extract total_debt (try total_debt first, then sum short+long term)
    total_debt: Optional[float] = None
    val = getattr(latest, "total_debt", None)
    if val is not None:
        try:
            total_debt = float(val)
        except (ValueError, TypeError):
            pass

    if total_debt is None:
        short = 0.0
        long = 0.0
        for attr in ("current_debt", "short_term_debt"):
            v = getattr(latest, attr, None)
            if v is not None:
                try:
                    short = float(v)
                    break
                except (ValueError, TypeError):
                    pass
        for attr in ("long_term_debt",):
            v = getattr(latest, attr, None)
            if v is not None:
                try:
                    long = float(v)
                    break
                except (ValueError, TypeError):
                    pass
        if short > 0 or long > 0:
            total_debt = short + long

    logger.info(
        "OpenBB balance for %s: total_assets=%s, total_debt=%s",
        symbol, total_assets, total_debt,
    )

    return {
        "total_assets": total_assets,
        "total_debt": total_debt,
        "source": "openbb/yfinance",
    }


async def get_audit_data(symbol: str) -> dict:
    """
    Async wrapper — returns balance sheet data from OpenBB SDK.

    Returns empty results if OpenBB is not installed, so the backend
    always starts and the audit falls back to FMP/yfinance.
    """
    if _obb is None:
        return _EMPTY
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _fetch_balance_openbb, symbol)
