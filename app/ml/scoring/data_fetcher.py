"""
Scoring Data Fetcher — aggregates all raw data needed for investment scoring.

Data sources:
  - FMP (primary): balance sheet (2 years), income statement (2 years), company profile
  - yfinance: market data, cashflow, earnings history, price history for RSI

Cache: TTL 6 hours, namespace "scoring_data".
"""

import asyncio
from typing import Any, Optional

import pandas as pd

from app.core.cache import cache_get, cache_set
from app.core.logging import logger
from app.integration import yfinance_client
from app.integration.fmp_client import (
    get_balance_sheet as fmp_balance_sheet,
    get_income_statement as fmp_income_statement,
    get_company_profile as fmp_company_profile,
)

_CACHE_NS = "scoring_data"
_CACHE_TTL = 21_600  # 6 hours


def _safe_pd(val: Any) -> Optional[float]:
    """Safe float conversion handling pandas NA values."""
    if val is None:
        return None
    try:
        import math
        f = float(val)
        return None if math.isnan(f) or math.isinf(f) else f
    except (ValueError, TypeError):
        return None


def _compute_rsi(close: pd.Series, window: int = 14) -> Optional[float]:
    if len(close) < window + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))
    avg_gain = gain.ewm(com=window - 1, min_periods=window).mean()
    avg_loss = loss.ewm(com=window - 1, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    rsi_series = 100 - (100 / (1 + rs))
    last = rsi_series.dropna()
    return float(last.iloc[-1]) if not last.empty else None


async def fetch_scoring_data(symbol: str) -> dict:
    """
    Fetch and assemble all raw data needed for scoring.

    Returns a flat dict with standardised keys used by all scoring modules.
    Cached for 6 hours.
    """
    cached = cache_get(_CACHE_NS, symbol)
    if cached is not None:
        logger.info("scoring_data/%s: cache hit", symbol)
        return cached

    # Parallel fetch: FMP + yfinance info
    fmp_results = await asyncio.gather(
        fmp_balance_sheet(symbol, limit=2),
        fmp_income_statement(symbol, limit=2),
        fmp_company_profile(symbol),
        yfinance_client.get_ticker_info(symbol),
        return_exceptions=True,
    )

    fmp_bs_raw = fmp_results[0] if not isinstance(fmp_results[0], Exception) else None
    fmp_is_raw = fmp_results[1] if not isinstance(fmp_results[1], Exception) else None
    fmp_profile_raw = fmp_results[2] if not isinstance(fmp_results[2], Exception) else None
    yf_info = fmp_results[3] if isinstance(fmp_results[3], dict) else {}

    # Cashflow + history + calendar
    cf_results = await asyncio.gather(
        yfinance_client.get_cashflow(symbol),
        yfinance_client.get_history(symbol, period="1y"),
        yfinance_client.get_calendar(symbol),
        return_exceptions=True,
    )

    yf_cashflow = cf_results[0] if isinstance(cf_results[0], dict) else {}
    yf_history = cf_results[1] if isinstance(cf_results[1], pd.DataFrame) else pd.DataFrame()
    yf_calendar = cf_results[2] if isinstance(cf_results[2], dict) else {}

    # yfinance fallback for balance sheet / income stmt
    yfbs_results = await asyncio.gather(
        yfinance_client.get_balance_sheet(symbol),
        yfinance_client.get_income_stmt(symbol),
        return_exceptions=True,
    )
    yf_bs = yfbs_results[0] if isinstance(yfbs_results[0], dict) else {}
    yf_is = yfbs_results[1] if isinstance(yfbs_results[1], dict) else {}

    # ── Helpers ──────────────────────────────────────────────────────
    fmp_bs_cur: dict = (fmp_bs_raw[0] if fmp_bs_raw and len(fmp_bs_raw) > 0 else {})
    fmp_bs_prev: dict = (fmp_bs_raw[1] if fmp_bs_raw and len(fmp_bs_raw) > 1 else {})
    fmp_is_cur: dict = (fmp_is_raw[0] if fmp_is_raw and len(fmp_is_raw) > 0 else {})
    fmp_is_prev: dict = (fmp_is_raw[1] if fmp_is_raw and len(fmp_is_raw) > 1 else {})
    profile: dict = fmp_profile_raw or {}

    def _bs(fmp_key: str, yf_key: str, prev: bool = False) -> Optional[float]:
        src = fmp_bs_prev if prev else fmp_bs_cur
        val = _safe_pd(src.get(fmp_key))
        if val is None:
            val = _safe_pd((yf_bs or {}).get(yf_key))
        return val

    def _is(fmp_key: str, yf_key: str, prev: bool = False) -> Optional[float]:
        src = fmp_is_prev if prev else fmp_is_cur
        val = _safe_pd(src.get(fmp_key))
        if val is None:
            val = _safe_pd((yf_is or {}).get(yf_key))
        return val

    # ── RSI from price history ────────────────────────────────────
    rsi_14: Optional[float] = None
    if not yf_history.empty:
        close_col = "Close" if "Close" in yf_history.columns else "close"
        if close_col in yf_history.columns:
            rsi_14 = _compute_rsi(yf_history[close_col])

    # ── Earnings beat rate ────────────────────────────────────────
    beat_count = 0
    total_count = 0
    for entry in (yf_calendar.get("history") or []):
        if entry.get("beat") is not None:
            total_count += 1
            if entry["beat"]:
                beat_count += 1

    # ── Balance sheet ─────────────────────────────────────────────
    total_assets = _bs("totalAssets", "Total Assets")
    total_assets_prev = _bs("totalAssets", "Total Assets", prev=True)
    total_liabilities = _bs("totalLiabilities", "Total Liabilities Net Minority Interest")
    total_debt = _bs("totalDebt", "Total Debt")
    total_debt_prev = _bs("totalDebt", "Total Debt", prev=True)
    current_assets = _bs("totalCurrentAssets", "Current Assets")
    current_assets_prev = _bs("totalCurrentAssets", "Current Assets", prev=True)
    current_liabilities = _bs("totalCurrentLiabilities", "Current Liabilities")
    current_liabilities_prev = _bs("totalCurrentLiabilities", "Current Liabilities", prev=True)
    retained_earnings = _bs("retainedEarnings", "Retained Earnings")
    shares_outstanding = (
        _bs("commonStockSharesOutstanding", "Ordinary Shares Number")
        or _bs("weightedAverageShsOut", "Share Issued")
        or _safe_pd(yf_info.get("sharesOutstanding"))
    )
    shares_outstanding_prev = (
        _bs("commonStockSharesOutstanding", "Ordinary Shares Number", prev=True)
        or _bs("weightedAverageShsOut", "Share Issued", prev=True)
    )
    book_value_per_share = _safe_pd(yf_info.get("bookValue"))
    cash = _bs("cashAndCashEquivalents", "Cash And Cash Equivalents")

    # ── Income statement ──────────────────────────────────────────
    revenue = _is("revenue", "Total Revenue")
    revenue_prev = _is("revenue", "Total Revenue", prev=True)
    net_income = _is("netIncome", "Net Income")
    net_income_prev = _is("netIncome", "Net Income", prev=True)
    gross_profit = _is("grossProfit", "Gross Profit")
    gross_profit_prev = _is("grossProfit", "Gross Profit", prev=True)
    ebit = _is("operatingIncome", "Operating Income")
    eps = _safe_pd(fmp_is_cur.get("eps")) or _safe_pd(yf_info.get("trailingEps"))
    eps_prev = _safe_pd(fmp_is_prev.get("eps"))

    # ── Cashflow ──────────────────────────────────────────────────
    cf = yf_cashflow or {}
    operating_cash_flow = (
        _safe_pd(cf.get("Operating Cash Flow"))
        or _safe_pd(cf.get("Total Cash From Operating Activities"))
        or _safe_pd(fmp_is_cur.get("operatingCashFlow"))
    )
    capital_expenditures = (
        _safe_pd(cf.get("Capital Expenditure"))
        or _safe_pd(cf.get("Capital Expenditures"))
        or _safe_pd(fmp_is_cur.get("capitalExpenditure"))
    )

    data = {
        "symbol": symbol,
        "company_name": profile.get("companyName") or yf_info.get("longName"),
        "sector": profile.get("sector") or yf_info.get("sector"),
        "industry": profile.get("industry") or yf_info.get("industry"),
        # Market
        "current_price": _safe_pd(yf_info.get("currentPrice") or yf_info.get("regularMarketPrice")),
        "market_cap": _safe_pd(yf_info.get("marketCap")),
        "beta": _safe_pd(yf_info.get("beta")),
        "pe_ratio": _safe_pd(yf_info.get("trailingPE")),
        "forward_pe": _safe_pd(yf_info.get("forwardPE")),
        "peg_ratio": _safe_pd(yf_info.get("pegRatio")),
        "target_mean_price": _safe_pd(yf_info.get("targetMeanPrice")),
        "fifty_two_week_high": _safe_pd(yf_info.get("fiftyTwoWeekHigh")),
        "fifty_two_week_low": _safe_pd(yf_info.get("fiftyTwoWeekLow")),
        "two_hundred_day_average": _safe_pd(yf_info.get("twoHundredDayAverage")),
        "fifty_day_average": _safe_pd(yf_info.get("fiftyDayAverage")),
        "rsi_14": rsi_14,
        # Income
        "revenue": revenue,
        "revenue_prev": revenue_prev,
        "net_income": net_income,
        "net_income_prev": net_income_prev,
        "gross_profit": gross_profit,
        "gross_profit_prev": gross_profit_prev,
        "ebit": ebit,
        "eps": eps,
        "eps_prev": eps_prev,
        # Balance sheet
        "total_assets": total_assets,
        "total_assets_prev": total_assets_prev,
        "total_liabilities": total_liabilities,
        "total_debt": total_debt,
        "total_debt_prev": total_debt_prev,
        "current_assets": current_assets,
        "current_assets_prev": current_assets_prev,
        "current_liabilities": current_liabilities,
        "current_liabilities_prev": current_liabilities_prev,
        "retained_earnings": retained_earnings,
        "shares_outstanding": shares_outstanding,
        "shares_outstanding_prev": shares_outstanding_prev,
        "book_value_per_share": book_value_per_share,
        "cash": cash,
        # Cashflow
        "operating_cash_flow": operating_cash_flow,
        "capital_expenditures": capital_expenditures,
        # Earnings
        "earnings_beats": beat_count,
        "earnings_total": total_count,
    }

    cache_set(_CACHE_NS, symbol, data, ttl=_CACHE_TTL)
    logger.info(
        "scoring_data/%s: assembled (sector=%s, revenue=%s, rsi=%.1f)",
        symbol, data["sector"], data["revenue"], rsi_14 or 0,
    )
    return data
