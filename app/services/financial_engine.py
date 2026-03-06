from typing import Any, Optional

import numpy as np
import pandas as pd
import ta

from app.core.logging import logger
from app.models.schemas import Fundamentals, Technicals, BollingerBands


def _safe_get(info: dict, key: str) -> Optional[float]:
    val = info.get(key)
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _pct(val: Optional[float]) -> Optional[float]:
    """Convert a decimal ratio to a rounded percentage."""
    if val is None:
        return None
    return round(val * 100, 2)


def _extract_domain(url: Optional[str]) -> Optional[str]:
    """Return the bare domain from a full URL (e.g. 'https://www.apple.com/fr' → 'apple.com')."""
    if not url:
        return None
    try:
        from urllib.parse import urlparse
        host = urlparse(url).hostname or ""
        # Strip leading 'www.'
        if host.startswith("www."):
            host = host[4:]
        return host or None
    except Exception:
        return None


def compute_fundamentals(info: dict[str, Any]) -> Fundamentals:
    logger.info("Computing fundamentals for %s", info.get("symbol", "?"))

    # 3-year Revenue CAGR
    revenue_cagr = None
    revenue_history = info.get("revenueHistory")
    if revenue_history and len(revenue_history) >= 4:
        try:
            earliest = revenue_history[-1]
            latest = revenue_history[0]
            if earliest > 0 and latest > 0:
                revenue_cagr = round(((latest / earliest) ** (1 / 3) - 1) * 100, 2)
        except (TypeError, ZeroDivisionError):
            pass

    # FCF margin
    fcf = _safe_get(info, "freeCashflow")
    total_rev = _safe_get(info, "totalRevenue")
    fcf_margin = None
    if fcf is not None and total_rev and total_rev > 0:
        fcf_margin = round((fcf / total_rev) * 100, 2)

    return Fundamentals(
        market_cap=_safe_get(info, "marketCap"),
        pe_ratio=_safe_get(info, "trailingPE"),
        peg_ratio=_safe_get(info, "pegRatio"),
        roe=_pct(_safe_get(info, "returnOnEquity")),
        roa=_pct(_safe_get(info, "returnOnAssets")),
        net_margin=_pct(_safe_get(info, "profitMargins")),
        gross_margin=_pct(_safe_get(info, "grossMargins")),
        fcf_margin=fcf_margin,
        debt_to_equity=_safe_get(info, "debtToEquity"),
        revenue_cagr_3y=revenue_cagr,
        currency=info.get("currency"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        name=info.get("longName") or info.get("shortName"),
        website=_extract_domain(info.get("website")),
    )


def compute_technicals(df: pd.DataFrame) -> Technicals:
    """Calculate RSI-14, SMA 50/200, Bollinger Bands, and Max Drawdown from OHLC data."""
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
