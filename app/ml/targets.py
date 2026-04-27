"""
Target Construction — Future price change for J+1.

The target is the percentage change of `close` 1 day into the future,
computed via shift(-1) to prevent any data leakage.

Input:  DataFrame with at least a `close` column.
Output: DataFrame with target column added, trailing NaN row dropped.
"""

import pandas as pd

from app.core.logging import logger


# Horizon definitions: column name → shift period
HORIZONS = {
    "target_1d": 1,
}

TARGET_COLUMNS = list(HORIZONS.keys())


def build_targets(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a forward-looking target column to the DataFrame.

    For horizon N, the target is:
        target_Nd = (close[t+N] - close[t]) / close[t]

    This is equivalent to close.pct_change(N).shift(-N).

    The last N rows will have NaN targets and are dropped.

    Args:
        df: DataFrame with a `close` column (and ideally feature columns).

    Returns:
        New DataFrame with target column added and trailing NaN rows removed.
        The original DataFrame is not mutated.

    Raises:
        ValueError: If `close` column is missing or data is too short.
    """
    if "close" not in df.columns:
        raise ValueError("DataFrame must contain a 'close' column.")

    max_horizon = max(HORIZONS.values())
    if len(df) <= max_horizon:
        raise ValueError(
            f"Need more than {max_horizon} rows to build targets, got {len(df)}."
        )

    out = df.copy()

    for col_name, period in HORIZONS.items():
        out[col_name] = out["close"].pct_change(periods=period).shift(-period)

    rows_before = len(out)
    out = out.dropna(subset=TARGET_COLUMNS).reset_index(drop=True)
    rows_after = len(out)

    horizons_str = "/".join(f"{p}d" for p in HORIZONS.values())
    logger.info(
        "Targets built: %d → %d rows (%d trailing rows dropped, horizons: %s)",
        rows_before, rows_after, rows_before - rows_after, horizons_str,
    )

    return out
