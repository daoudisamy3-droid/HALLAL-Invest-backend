from typing import Any, Optional
from urllib.parse import urlparse

import numpy as np
import pandas as pd
import ta

from app.core.logging import logger
from app.models.schemas import Fundamentals, Technicals, BollingerBands


def _safe_float(info: dict, key: str) -> Optional[float]:
    """Extract a float from yfinance info dict. Returns None if missing or invalid."""
    val = info.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _extract_domain(url: Optional[str]) -> Optional[str]:
    """Return the bare domain from a full URL (e.g. 'https://www.apple.com/fr' -> 'apple.com')."""
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


def extract_fundamentals(info: dict[str, Any]) -> Fundamentals:
    """Extract raw native fields from yfinance info dict. No derivation, no gap-filling."""
    logger.info("Extracting fundamentals for %s", info.get("symbol", "?"))

    return Fundamentals(
        name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        currency=info.get("currency"),
        website=_extract_domain(info.get("website")),
        market_cap=_safe_float(info, "marketCap"),
        trailing_pe=_safe_float(info, "trailingPE"),
        forward_pe=_safe_float(info, "forwardPE"),
        peg_ratio=_safe_float(info, "pegRatio"),
        dividend_yield=_safe_float(info, "dividendYield"),
        return_on_equity=_safe_float(info, "returnOnEquity"),
        gross_margins=_safe_float(info, "grossMargins"),
        debt_to_equity=_safe_float(info, "debtToEquity"),
        total_debt=_safe_float(info, "totalDebt"),
        total_revenue=_safe_float(info, "totalRevenue"),
        free_cashflow=_safe_float(info, "freeCashflow"),
    )


def compute_technicals(df: pd.DataFrame) -> Technicals:
    """Calculate RSI-14, SMA 50/200, Bollinger Bands, and Max Drawdown from raw OHLC data."""
    logger.info("Computing technicals (%d bars)", len(df))

    close = df["Close"]
    current_price = float(close.iloc[-1])

    # RSI (14-day)
    rsi_series = ta.momentum.RSIIndicator(close=close, window=14).rsi()
    rsi_14 = round(float(rsi_series.iloc[-1]), 2) if not rsi_series.empty else None

    # SMA 50 / 200
    sma_50_series = ta.trend.SMAIndicator(close=close, window=50).sma_indicator()
    sma_200_series = ta.trend.SMAIndicator(close=close, window=200).sma_indicator()
    sma_50 = round(float(sma_50_series.iloc[-1]), 2) if not sma_50_series.empty and not np.isnan(sma_50_series.iloc[-1]) else None
    sma_200 = round(float(sma_200_series.iloc[-1]), 2) if not sma_200_series.empty and not np.isnan(sma_200_series.iloc[-1]) else None

    # Bollinger Bands (20-day, 2 std)
    bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
    bb_upper = bb.bollinger_hband().iloc[-1]
    bb_middle = bb.bollinger_mavg().iloc[-1]
    bb_lower = bb.bollinger_lband().iloc[-1]
    bollinger = BollingerBands(
        upper=round(float(bb_upper), 2) if not np.isnan(bb_upper) else None,
        middle=round(float(bb_middle), 2) if not np.isnan(bb_middle) else None,
        lower=round(float(bb_lower), 2) if not np.isnan(bb_lower) else None,
    )

    # Max Drawdown (5-year)
    cummax = close.cummax()
    drawdown = (close - cummax) / cummax
    max_drawdown = round(float(drawdown.min()) * 100, 2)

    return Technicals(
        current_price=round(current_price, 2),
        rsi_14=rsi_14,
        sma_50=sma_50,
        sma_200=sma_200,
        bollinger_bands=bollinger,
        max_drawdown_5y=max_drawdown,
    )
