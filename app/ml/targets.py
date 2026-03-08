"""
Target Construction — Future price change for J+1, J+3, J+5.

Each target is the percentage change of `close` N days into the future,
computed via shift(-N) to prevent any data leakage.

Input:  DataFrame with at least a `close` column.
Output: DataFrame with target columns added, trailing NaN rows dropped.
"""

import pandas as pd

from app.core.logging import logger


# Horizon definitions: column name → shift period
HORIZONS = {
    "target_1d": 1,
    "target_3d": 3,
    "target_5d": 5,
}

TARGET_COLUMNS = list(HORIZONS.keys())


def build_targets(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add forward-looking target columns to the DataFrame.

    For each horizon N, the target is:
        target_Nd = (close[t+N] - close[t]) / close[t]

    This is equivalent to close.pct_change(N).shift(-N).

    The last N rows of the longest horizon (5d) will have NaN targets
    and are dropped to produce a clean training set.

    Args:
        df: DataFrame with a `close` column (and ideally feature columns).

    Returns:
        New DataFrame with target columns added and trailing NaN rows removed.
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
        # pct_change(period) looks backward by `period` rows,
        # so we shift(-period) to turn it into a forward-looking target.
        out[col_name] = out["close"].pct_change(periods=period).shift(-period)

    # Drop rows where any target is NaN (the last `max_horizon` rows)
    rows_before = len(out)
    out = out.dropna(subset=TARGET_COLUMNS).reset_index(drop=True)
    rows_after = len(out)

    logger.info(
        "Targets built: %d → %d rows (%d trailing rows dropped, horizons: 1d/3d/5d)",
        rows_before, rows_after, rows_before - rows_after,
    )

    return out
