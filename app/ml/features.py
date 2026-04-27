"""
Feature Engineering — Technical indicators + optional fundamental data.

Input:  DataFrame with columns [timestamp, open, high, low, close, volume]
        + optional `fundamentals` dict from /api/v1/ticker/{symbol}
Output: DataFrame with original columns + engineered features, NaN rows dropped.

Technical features (always computed):
  - close, volume          (raw)
  - return_1d              daily percentage return
  - volatility_10d         rolling 10-day std of return_1d
  - ma_5, ma_10, ma_20     simple moving averages of close

Fundamental features (require `fundamentals` dict; default 0.0 otherwise):
  - rsi_14                 14-day Relative Strength Index
  - trailing_pe            trailing price-to-earnings ratio
  - profit_margins         net profit margin
  - return_on_equity       return on equity
  - price_vs_52w_high      close / 52-week high  (range position)
  - price_vs_200ma         close / 200-day moving average
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
    "price_vs_52w_high",
    "price_vs_200ma",
]

_FUNDAMENTAL_COLS = [
    "rsi_14",
    "trailing_pe",
    "profit_margins",
    "return_on_equity",
    "price_vs_52w_high",
    "price_vs_200ma",
]


def build_features(
    df: pd.DataFrame,
    fundamentals: Optional[dict] = None,
) -> pd.DataFrame:
    """
    Add technical and (optionally) fundamental feature columns to the DataFrame.

    Args:
        df:           DataFrame with at least [close, volume] columns.
        fundamentals: Dict from /api/v1/ticker/{symbol} response.
                      Each key maps to {"value": float|None, ...}.
                      When None, fundamental columns are filled with 0.0
                      so the feature matrix shape stays consistent.

    Returns:
        New DataFrame with all FEATURE_COLUMNS added and NaN rows dropped.
        The original DataFrame is not mutated.

    Raises:
        ValueError: If required columns are missing or data is too short.
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

    # ── Daily return (percentage change) ──────────────────────────
    out["return_1d"] = out["close"].pct_change()

    # ── Rolling volatility (std of daily returns, 10-day window) ──
    out["volatility_10d"] = out["return_1d"].rolling(window=10).std()

    # ── Simple Moving Averages ────────────────────────────────────
    out["ma_5"] = out["close"].rolling(window=5).mean()
    out["ma_10"] = out["close"].rolling(window=10).mean()
    out["ma_20"] = out["close"].rolling(window=20).mean()

    # ── Fundamental features ──────────────────────────────────────
    if fundamentals is not None:
        # price_vs_52w_high : position in the annual range (close / 52w high)
        high_52w = fundamentals.get("fifty_two_week_high", {}).get("value")
        if high_52w and high_52w > 0:
            out["price_vs_52w_high"] = out["close"] / high_52w
        else:
            out["price_vs_52w_high"] = 1.0

        # price_vs_200ma : distance from long-term moving average
        ma_200 = fundamentals.get("two_hundred_day_average", {}).get("value")
        if ma_200 and ma_200 > 0:
            out["price_vs_200ma"] = out["close"] / ma_200
        else:
            out["price_vs_200ma"] = 1.0

        # Scalar fundamentals broadcast across all rows
        for col, key in [
            ("rsi_14",           "rsi_14"),
            ("trailing_pe",      "trailing_pe"),
            ("profit_margins",   "profit_margins"),
            ("return_on_equity", "return_on_equity"),
        ]:
            val = fundamentals.get(key, {}).get("value")
            out[col] = float(val) if val is not None else 0.0
    else:
        for col in _FUNDAMENTAL_COLS:
            out[col] = 0.0

    # ── Drop rows with NaN across all feature columns ─────────────
    rows_before = len(out)
    out = out.dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)
    rows_after = len(out)

    fundamental_mode = "with fundamentals" if fundamentals is not None else "fundamentals=None (zeros)"
    logger.info(
        "Features built (%s): %d → %d rows (%d dropped due to NaN)",
        fundamental_mode, rows_before, rows_after, rows_before - rows_after,
    )

    return out
