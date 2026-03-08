"""
Feature Engineering — Technical indicators from OHLCV data.

Input:  DataFrame with columns [timestamp, open, high, low, close, volume]
Output: DataFrame with original columns + engineered features, NaN rows dropped.

Features:
  - close        (raw)
  - volume       (raw)
  - return_1d    daily percentage return
  - volatility_10d  rolling 10-day std of return_1d
  - ma_5         5-day simple moving average of close
  - ma_10        10-day simple moving average of close
  - ma_20        20-day simple moving average of close
"""

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
]


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add technical indicator columns to the OHLCV DataFrame.

    Args:
        df: DataFrame with at least [close, volume] columns.

    Returns:
        New DataFrame with feature columns added and NaN rows dropped.
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

    # ── Drop rows with NaN (first 20 rows won't have ma_20) ──────
    rows_before = len(out)
    out = out.dropna(subset=FEATURE_COLUMNS).reset_index(drop=True)
    rows_after = len(out)

    logger.info(
        "Features built: %d → %d rows (%d dropped due to NaN from rolling windows)",
        rows_before, rows_after, rows_before - rows_after,
    )

    return out
