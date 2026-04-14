"""
Risk Core Engine – raw fundamentals for the ANALYZE tab.

Strictly a data provider: the frontend owns the scoring logic. We just
fetch, normalise, and return the 3-block contract (valuation / health /
growth). yfinance is the single source of truth. Missing fields are
returned as None so the frontend can show null placeholders.

60-second TTL in-memory cache per symbol. Never raises: any exception
during fetch collapses to an all-null payload.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from app.core.logging import logger

_executor = ThreadPoolExecutor(max_workers=4)


# ── Sector P/E benchmarks (industryAvgPe surrogate) ──────────────
#
# yfinance does not expose a reliable free industry-P/E endpoint.
# We ship long-term sector averages so the frontend always has a
# deterministic benchmark to compare against.
_SECTOR_PE_BENCHMARK: dict[str, float] = {
    "Technology": 28.0,
    "Healthcare": 22.0,
    "Financial Services": 14.0,
    "Consumer Cyclical": 20.0,
    "Consumer Defensive": 22.0,
    "Communication Services": 18.0,
    "Industrials": 20.0,
    "Energy": 12.0,
    "Utilities": 18.0,
    "Real Estate": 25.0,
    "Basic Materials": 16.0,
}


# ── 60-second TTL cache ──────────────────────────────────────────

_CACHE_TTL = 60  # seconds
_risk_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_get(symbol: str) -> Optional[dict[str, Any]]:
    entry = _risk_cache.get(symbol)
    if entry is None:
        return None
    ts, data = entry
    if (datetime.now(timezone.utc).timestamp() - ts) > _CACHE_TTL:
        return None
    return data


def _cache_set(symbol: str, data: dict[str, Any]) -> None:
    _risk_cache[symbol] = (datetime.now(timezone.utc).timestamp(), data)


# ── Helpers ──────────────────────────────────────────────────────


def _safe(val: Any) -> Optional[float]:
    """Coerce to float; return None on null/NaN/invalid."""
    if val is None:
        return None
    try:
        f = float(val)
        if pd.isna(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _round(val: Optional[float], decimals: int = 2) -> Optional[float]:
    if val is None:
        return None
    return round(val, decimals)


def _empty_payload() -> dict[str, Any]:
    """
    Graceful-degradation fallback returned whenever data fetch fails.
    All metrics are None EXCEPT industryAvgPe which defaults to 15.0
    so the frontend gauges can still render a neutral baseline.
    """
    return {
        "valuation": {
            "pe": None,
            "ps": None,
            "forwardPe": None,
            "industryAvgPe": 15.0,
        },
        "health": {
            "debtToEquity": None,
            "currentRatio": None,
            "fcfYield": None,
        },
        "growth": {
            "epsGrowth3Y": None,
            "revGrowth3Y": None,
        },
    }


# ── 3-year CAGR from income statement ────────────────────────────


def _cagr_from_series(latest: Any, oldest: Any, years: int) -> Optional[float]:
    """
    Compound Annual Growth Rate as a percentage (e.g. 12.5 for +12.5%/yr).
    Returns None if either value is missing, non-positive, or years <= 0.
    """
    a = _safe(latest)
    b = _safe(oldest)
    if a is None or b is None or b <= 0 or a <= 0 or years <= 0:
        return None
    try:
        cagr = (a / b) ** (1.0 / years) - 1.0
    except (ValueError, ZeroDivisionError):
        return None
    return round(cagr * 100.0, 2)


def _extract_3y_growth(ticker: yf.Ticker) -> tuple[Optional[float], Optional[float]]:
    """
    Compute 3Y EPS and revenue CAGR from yfinance annual income statement.

    yfinance typically returns 4 annual columns (current + 3 prior), sorted
    most-recent first. We take column 0 as the latest and the last column
    as the oldest, and infer the year span from the column count.
    """
    try:
        inc = ticker.income_stmt
    except Exception as exc:
        logger.debug("income_stmt fetch failed: %s", exc)
        return None, None

    if inc is None or not hasattr(inc, "empty") or inc.empty or inc.shape[1] < 2:
        return None, None

    cols = list(inc.columns)
    years = max(1, len(cols) - 1)  # e.g. 4 cols → 3 years span

    latest = inc.iloc[:, 0]
    oldest = inc.iloc[:, -1]

    # ── Revenue CAGR ─────────────────────────────────────────────
    rev_new = latest.get("Total Revenue")
    rev_old = oldest.get("Total Revenue")
    rev_growth = _cagr_from_series(rev_new, rev_old, years)

    # ── EPS CAGR (Diluted preferred, then Basic) ────────────────
    eps_new = latest.get("Diluted EPS")
    if _safe(eps_new) is None:
        eps_new = latest.get("Basic EPS")
    eps_old = oldest.get("Diluted EPS")
    if _safe(eps_old) is None:
        eps_old = oldest.get("Basic EPS")
    eps_growth = _cagr_from_series(eps_new, eps_old, years)

    return eps_growth, rev_growth


# ── Main blocking fetcher ────────────────────────────────────────


def _normalise_debt_to_equity(raw: Optional[float]) -> Optional[float]:
    """
    yfinance reports debtToEquity scaled inconsistently:
      - Most tickers: percentage form (178.92 → ratio 1.79)
      - Some tickers: decimal form already (1.79)
    Heuristic: if |raw| > 10, assume it's a percentage and divide by 100.
    """
    if raw is None:
        return None
    if abs(raw) > 10.0:
        return round(raw / 100.0, 2)
    return round(raw, 2)


def _fetch_risk_blocking(symbol: str) -> dict[str, Any]:
    """
    Blocking yfinance fetch. Returns the strict 3-block contract.

    Graceful degradation: ANY exception anywhere in this function is
    caught at the top level and collapses to the fallback shell
    (all None except industryAvgPe=15.0). The endpoint therefore
    always responds HTTP 200 with a valid contract.
    """
    try:
        # ── 1. Ticker + info ─────────────────────────────────────
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        # ── 2. Valuation ─────────────────────────────────────────
        pe = _safe(info.get("trailingPE"))
        ps = _safe(info.get("priceToSalesTrailing12Months"))
        forward_pe = _safe(info.get("forwardPE"))
        sector = info.get("sector")
        # Default to 15.0 (neutral baseline) when sector is unknown
        industry_avg_pe = _SECTOR_PE_BENCHMARK.get(sector or "", 15.0)

        # ── 3. Health ────────────────────────────────────────────
        debt_to_equity = _normalise_debt_to_equity(_safe(info.get("debtToEquity")))
        current_ratio = _safe(info.get("currentRatio"))

        fcf = _safe(info.get("freeCashflow"))
        market_cap = _safe(info.get("marketCap"))
        fcf_yield: Optional[float] = None
        if fcf is not None and market_cap is not None and market_cap > 0:
            fcf_yield = round((fcf / market_cap) * 100.0, 2)

        # ── 4. Growth (3Y CAGR from annual income statement) ─────
        try:
            eps_growth_3y, rev_growth_3y = _extract_3y_growth(ticker)
        except Exception as growth_exc:
            # Sub-failure: keep rest of the payload, null growth only
            logger.debug("risk-core: 3Y growth calc failed for %s: %s", symbol, growth_exc)
            eps_growth_3y = None
            rev_growth_3y = None

        payload = {
            "valuation": {
                "pe": _round(pe),
                "ps": _round(ps),
                "forwardPe": _round(forward_pe),
                "industryAvgPe": industry_avg_pe,
            },
            "health": {
                "debtToEquity": debt_to_equity,
                "currentRatio": _round(current_ratio),
                "fcfYield": fcf_yield,
            },
            "growth": {
                "epsGrowth3Y": eps_growth_3y,
                "revGrowth3Y": rev_growth_3y,
            },
        }

        logger.info(
            "risk-core: %s pe=%s ps=%s fwdPe=%s d/e=%s cr=%s fcfY=%s epsG=%s revG=%s",
            symbol, pe, ps, forward_pe, debt_to_equity, current_ratio,
            fcf_yield, eps_growth_3y, rev_growth_3y,
        )
        return payload

    except Exception as e:
        # Top-level safety net: yfinance / network / parsing / anything.
        # We log loudly and return the all-null fallback so the frontend
        # can still render the scorecard at zero.
        print(f"Erreur fetch {symbol}: {e}")
        logger.error("risk-core: fatal fetch error for %s: %s", symbol, e)
        return _empty_payload()


# ── Public async API ─────────────────────────────────────────────


async def get_risk_core(symbol: str) -> dict[str, Any]:
    """
    Fetch the Risk Core contract (valuation / health / growth).
    60s in-memory cache. Never raises – returns all-null shell on error.
    """
    cached = _cache_get(symbol)
    if cached is not None:
        logger.info("risk-core: cache hit for %s", symbol)
        return cached

    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(_executor, _fetch_risk_blocking, symbol)
    except Exception as exc:
        logger.error("risk-core: unexpected error for %s: %s", symbol, exc)
        data = _empty_payload()

    _cache_set(symbol, data)
    return data
