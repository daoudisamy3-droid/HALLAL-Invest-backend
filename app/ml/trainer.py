"""
Training Pipeline — 3 independent RandomForestRegressor models.

Pipeline:  fetch_ohlcv → build_features → build_targets → train & evaluate.

Evaluation uses TimeSeriesSplit (n_splits=5) — NO shuffle — to respect
temporal ordering and prevent future data from leaking into training folds.

Returns trained models + MAE metrics per horizon.
"""

import asyncio
from typing import Any

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.model_selection import TimeSeriesSplit

from app.core.logging import logger
from app.ml.data_loader import fetch_ohlcv
from app.ml.features import build_features, FEATURE_COLUMNS
from app.ml.targets import build_targets, HORIZONS, TARGET_COLUMNS


# ── Model hyperparameters ────────────────────────────────────────

_RF_PARAMS = {
    "n_estimators": 300,
    "max_depth": 10,
    "random_state": 42,
    "n_jobs": -1,
}

_N_SPLITS = 5


async def train_models(symbol: str) -> dict[str, Any]:
    """
    Full training pipeline for a single symbol.

    Steps:
        1. Fetch ~1000 daily bars from Alpaca
        2. Compute technical features (close, volume, return, vol, MAs)
        3. Build forward-looking targets (1d, 3d, 5d)
        4. For each horizon:
           a. TimeSeriesSplit cross-validation (5 folds, no shuffle)
           b. Compute MAE on each fold
           c. Final fit on all data for production predictions
        5. Return models + metrics

    Args:
        symbol: Ticker symbol (e.g. "AAPL").

    Returns:
        {
            "symbol": str,
            "models": {"target_1d": fitted_model, "target_3d": ..., "target_5d": ...},
            "metrics": {"target_1d": mae_float, "target_3d": ..., "target_5d": ...},
            "training_samples": int,
            "n_splits": int,
            "feature_columns": list[str],
        }

    Raises:
        ValueError/RuntimeError from data_loader if Alpaca fails.
    """
    logger.info("=== TRAINING START for %s ===", symbol)

    # ── Step 1: Data ingestion ────────────────────────────────────
    df_raw = await fetch_ohlcv(symbol, limit=1000)
    logger.info("Raw data: %d rows", len(df_raw))

    # ── Step 2: Feature engineering ───────────────────────────────
    df_feat = build_features(df_raw)
    logger.info("After features: %d rows", len(df_feat))

    # ── Step 3: Target construction ───────────────────────────────
    df_full = build_targets(df_feat)
    logger.info("After targets: %d rows (ready for training)", len(df_full))

    # ── Step 4: Train one model per horizon ───────────────────────
    X = df_full[FEATURE_COLUMNS].values
    tscv = TimeSeriesSplit(n_splits=_N_SPLITS)

    models: dict[str, RandomForestRegressor] = {}
    metrics: dict[str, float] = {}

    for target_col in TARGET_COLUMNS:
        y = df_full[target_col].values

        # Cross-validation — compute MAE across folds
        fold_maes: list[float] = []

        for fold_idx, (train_idx, val_idx) in enumerate(tscv.split(X)):
            X_train, X_val = X[train_idx], X[val_idx]
            y_train, y_val = y[train_idx], y[val_idx]

            fold_model = RandomForestRegressor(**_RF_PARAMS)
            fold_model.fit(X_train, y_train)

            y_pred = fold_model.predict(X_val)
            fold_mae = mean_absolute_error(y_val, y_pred)
            fold_maes.append(fold_mae)

            logger.debug(
                "%s fold %d/%d: train=%d, val=%d, MAE=%.6f",
                target_col, fold_idx + 1, _N_SPLITS,
                len(train_idx), len(val_idx), fold_mae,
            )

        avg_mae = float(np.mean(fold_maes))
        metrics[target_col] = round(avg_mae, 6)

        logger.info(
            "%s CV MAE: %.6f (folds: %s)",
            target_col, avg_mae,
            [round(m, 6) for m in fold_maes],
        )

        # Final model — fit on ALL data for production use
        final_model = RandomForestRegressor(**_RF_PARAMS)
        final_model.fit(X, y)
        models[target_col] = final_model

    result = {
        "symbol": symbol,
        "models": models,
        "metrics": metrics,
        "training_samples": len(df_full),
        "n_splits": _N_SPLITS,
        "feature_columns": FEATURE_COLUMNS,
    }

    metrics_str = " | ".join(f"{k}={v:.6f}" for k, v in metrics.items())
    logger.info("=== TRAINING END for %s: samples=%d, %s ===", symbol, len(df_full), metrics_str)

    return result
