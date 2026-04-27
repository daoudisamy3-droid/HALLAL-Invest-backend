"""
Training Pipeline — XGBClassifier with directional binary classification.

Pipeline:  fetch_ohlcv → build_features → build_targets → binarize → scale → train.

Target: 1 = price goes UP tomorrow (target_1d > 0), 0 = DOWN or flat.

Evaluation uses TimeSeriesSplit (n_splits=5) — NO shuffle — to respect
temporal ordering and prevent future data from leaking into training folds.
Metric: directional accuracy (% of correctly predicted UP/DOWN days).

Returns fitted XGBClassifier + StandardScaler + accuracy metrics.
"""

import asyncio
from typing import Any

import numpy as np
from sklearn.metrics import accuracy_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from xgboost import XGBClassifier

from app.core.logging import logger
from app.ml.data_loader import fetch_ohlcv
from app.ml.features import build_features, FEATURE_COLUMNS
from app.ml.targets import build_targets


# ── Model hyperparameters ────────────────────────────────────────

_XGB_PARAMS = {
    "n_estimators": 300,
    "max_depth": 4,
    "learning_rate": 0.05,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "eval_metric": "logloss",
    "random_state": 42,
    "n_jobs": -1,
}

_N_SPLITS = 5


async def train_models(symbol: str) -> dict[str, Any]:
    """
    Full training pipeline for a single symbol.

    Steps:
        1. Fetch ~1000 daily bars from Alpaca
        2. Compute technical features (13 columns)
        3. Build forward-looking target (1d return)
        4. Binarize: 1 = UP (return > 0), 0 = DOWN / flat
        5. StandardScaler fit on full X
        6. TimeSeriesSplit cross-validation (5 folds) → directional accuracy
        7. Final XGBClassifier fit on all scaled data
        8. Return model + scaler + metrics

    Args:
        symbol: Ticker symbol (e.g. "AAPL").

    Returns:
        {
            "symbol": str,
            "model": XGBClassifier (fitted),
            "scaler": StandardScaler (fitted),
            "metrics": {
                "directional_accuracy_cv": float,
                "directional_accuracy_std": float,
                "training_samples": int,
            },
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

    # ── Step 4: Binarize continuous return → UP / DOWN ────────────
    df_full["target_binary"] = (df_full["target_1d"] > 0).astype(int)
    TARGET_COL = "target_binary"

    # ── Step 5: Prepare feature matrix and scale ──────────────────
    X = df_full[FEATURE_COLUMNS].values
    y = df_full[TARGET_COL].values

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # ── Step 6: TimeSeriesSplit cross-validation ──────────────────
    tscv = TimeSeriesSplit(n_splits=_N_SPLITS)
    fold_accuracies: list[float] = []

    for fold_idx, (train_idx, val_idx) in enumerate(tscv.split(X_scaled)):
        X_train, X_val = X_scaled[train_idx], X_scaled[val_idx]
        y_train, y_val = y[train_idx], y[val_idx]

        fold_model = XGBClassifier(**_XGB_PARAMS)
        fold_model.fit(X_train, y_train)

        acc = accuracy_score(y_val, fold_model.predict(X_val))
        fold_accuracies.append(acc)

        logger.debug(
            "fold %d/%d: train=%d val=%d accuracy=%.4f",
            fold_idx + 1, _N_SPLITS,
            len(train_idx), len(val_idx), acc,
        )

    cv_mean = float(np.mean(fold_accuracies))
    cv_std = float(np.std(fold_accuracies))

    logger.info(
        "Directional Accuracy CV: %.3f ± %.3f (folds: %s)",
        cv_mean, cv_std,
        [round(a, 4) for a in fold_accuracies],
    )

    # ── Step 7: Final fit on all data ─────────────────────────────
    model = XGBClassifier(**_XGB_PARAMS)
    model.fit(X_scaled, y)

    result = {
        "symbol": symbol,
        "model": model,
        "scaler": scaler,
        "metrics": {
            "directional_accuracy_cv": round(cv_mean, 4),
            "directional_accuracy_std": round(cv_std, 4),
            "training_samples": len(df_full),
        },
        "n_splits": _N_SPLITS,
        "feature_columns": FEATURE_COLUMNS,
    }

    logger.info(
        "=== TRAINING END for %s: samples=%d acc=%.3f±%.3f ===",
        symbol, len(df_full), cv_mean, cv_std,
    )

    return result
