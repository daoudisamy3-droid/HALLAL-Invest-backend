"""
Feature Engineering — Technical indicators + fundamental data via YFinance.

Input:  DataFrame with columns [timestamp, open, high, low, close, volume]
        + optional symbol string for live YFinance fundamental fetch.
Output: DataFrame with original columns + engineered features, NaN rows dropped.

Technical features (always computed from OHLCV):
  - close, volume          (raw)
  - return_1d              daily percentage return
  - volatility_10d         rolling 10-day std of return_1d
  - ma_5, ma_10, ma_20     simple moving averages of close
  - rsi_14                 14-day Relative Strength Index
  - pos_vs_52w_high        close / rolling 252-day max
  - pos_vs_ma200           close / rolling 200-day mean

Fundamental features (from YFinance info when symbol provided; 0.0 otherwise):
  - trailing_pe            trailing price-to-earnings ratio
  - profit_margins         net profit margin
  - return_on_equity       return on equity
"""

from typing import Optional

import pandas as pd

from app.core.logging import logger


# Feature column names exported for use by trainer/predictor
FEATURE_COLUMNS = [
    "close",
    "volume",
    "return_1d",
    "volatility_10d",
    "ma_5",
    "ma_10",
    "ma_20",
    "rsi_14",
    "trailing_pe",
    "profit_margins",
    "return_on_equity",
    "pos_vs_52w_high",
    "pos_vs_ma200",
]


def _compute_rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def build_features(
    df: pd.DataFrame,
    symbol: Optional[str] = None,
    fundamentals: Optional[dict] = None,  # kept for backwards compat, ignored when symbol given
) -> pd.DataFrame:
    """
    Add technical and fundamental feature columns to the DataFrame.

    Args:
        df:           DataFrame with at least [close, volume] columns.
        symbol:       Ticker symbol. When provided, fetches trailing_pe,
                      profit_margins, return_on_equity from YFinance info.
        fundamentals: Deprecated — ignored when symbol is provided.

    Returns:
        New DataFrame with all FEATURE_COLUMNS added and NaN rows dropped.
    """
    required = {"close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"DataFrame missing required columns: {missing}")

    if len(df) < 21:
        raise ValueError(
            f"Need at least 21 rows to compute ma_20, got {len(df)}. "
            "Fetch more historical data."
        )

    out = df.copy()

    # ── Daily return ──────────────────────────────────────────────
    out["return_1d"] = out["close"].pct_change()

    # ── Rolling volatility ────────────────────────────────────────
    out["volatility_10d"] = out["return_1d"].rolling(window=10).std()

    # ── Simple Moving Averages ────────────────────────────────────
    out["ma_5"] = out["close"].rolling(window=5).mean()
    out["ma_10"] = out["close"].rolling(window=10).mean()
    out["ma_20"] = out["close"].rolling(window=20).mean()

    # ── RSI 14 (OHLCV-based time series) ─────────────────────────
    if "rsi_14" not in out.columns:
        out["rsi_14"] = _compute_rsi(out["close"], period=14)

    # ── Position vs 52-week high (rolling 252-day max) ────────────
    out["pos_vs_52w_high"] = out["close"] / out["close"].rolling(252).max()

    # ── Position vs MA200 (rolling 200-day mean) ──────────────────
    out["pos_vs_ma200"] = out["close"] / out["close"].rolling(200).mean()

    # ── Scalar fundamentals ───────────────────────────────────────
    trailing_pe = 0.0
    profit_margins = 0.0
    return_on_equity = 0.0

    if symbol:
        try:
            import yfinance as yf
            info = yf.Ticker(symbol).info or {}
            trailing_pe = float(info.get("trailingPE") or 0)
            profit_margins = float(info.get("profitMargins") or 0)
            return_on_equity = float(info.get("returnOnEquity") or 0)
            logger.info(
                "features/%s: pe=%.2f margins=%.4f roe=%.4f",
                symbol, trailing_pe, profit_margins, return_on_equity,
            )
        except Exception as exc:
            logger.warning("features/%s: yfinance info failed: %s", symbol, exc)

    out["trailing_pe"] = trailing_pe
    out["profit_margins"] = profit_margins
    out["return_on_equity"] = return_on_equity

    # ── Drop NaN rows ─────────────────────────────────────────────
    rows_before = len(out)
    out = out.dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)
    rows_after = len(out)

    logger.info(
        "Features built (symbol=%s): %d → %d rows (%d dropped due to NaN)",
        symbol or "None", rows_before, rows_after, rows_before - rows_after,
    )

    return out
