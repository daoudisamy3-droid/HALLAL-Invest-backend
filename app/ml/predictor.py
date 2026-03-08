"""
Prediction Service — Cached model inference with 24h TTL.

Manages an in-memory cache of trained models per symbol.
On cache miss (or TTL expiry), triggers a full training pipeline.
Predictions are computed from the latest row of feature data.
"""

import time
from typing import Any

from app.core.logging import logger
from app.ml.data_loader import fetch_ohlcv
from app.ml.features import build_features, FEATURE_COLUMNS
from app.ml.targets import TARGET_COLUMNS
from app.ml.trainer import train_models


# ── In-memory model cache ────────────────────────────────────────
# {symbol: {"trained_at": float, "payload": train_models() result}}
_model_cache: dict[str, dict[str, Any]] = {}

_CACHE_TTL = 86_400  # 24 hours in seconds


def _is_cache_valid(symbol: str) -> bool:
    """Check if cached models exist and are younger than TTL."""
    entry = _model_cache.get(symbol)
    if entry is None:
        return False
    age = time.time() - entry["trained_at"]
    if age >= _CACHE_TTL:
        logger.info("Cache expired for %s (age=%.0fs)", symbol, age)
        return False
    return True


async def get_prediction(symbol: str) -> dict[str, Any]:
    """
    Return price predictions for J+1, J+3, J+5.

    Flow:
        1. If models for `symbol` are cached and < 24h old → reuse.
           Otherwise → call train_models(symbol) and cache result.
        2. Fetch latest OHLCV data, build features, take last row.
        3. Run each model on the last-row feature vector.
        4. Convert percentage predictions into projected prices.

    Args:
        symbol: Ticker symbol (e.g. "AAPL").

    Returns:
        {
            "symbol": str,
            "current_price": float,
            "predictions": {
                "1d": {"price": float, "pct": float},
                "3d": {"price": float, "pct": float},
                "5d": {"price": float, "pct": float},
            },
            "model_age_seconds": float,
        }

    Raises:
        RuntimeError: If data fetch or prediction fails.
    """
    symbol = symbol.upper().strip()

    # ── Step 1: Ensure models are trained and cached ──────────────
    if not _is_cache_valid(symbol):
        logger.info("Training models for %s (cache miss or expired)", symbol)
        payload = await train_models(symbol)
        _model_cache[symbol] = {
            "trained_at": time.time(),
            "payload": payload,
        }
    else:
        logger.info("Using cached models for %s", symbol)

    cache_entry = _model_cache[symbol]
    payload = cache_entry["payload"]
    models = payload["models"]
    model_age = time.time() - cache_entry["trained_at"]

    # ── Step 2: Get latest feature row ────────────────────────────
    df_raw = await fetch_ohlcv(symbol, limit=100)
    df_feat = build_features(df_raw)
    last_row = df_feat[FEATURE_COLUMNS].iloc[[-1]]  # keep as DataFrame
    current_price = float(df_feat["close"].iloc[-1])

    # ── Step 3: Predict for each horizon ──────────────────────────
    predictions: dict[str, dict[str, float]] = {}
    horizon_labels = {"target_1d": "1d", "target_3d": "3d", "target_5d": "5d"}

    for target_col in TARGET_COLUMNS:
        model = models[target_col]
        pct_change = float(model.predict(last_row.values)[0])
        predicted_price = round(current_price * (1 + pct_change), 2)

        label = horizon_labels[target_col]
        predictions[label] = {
            "price": predicted_price,
            "pct": round(pct_change * 100, 4),
        }

    logger.info(
        "Prediction for %s: price=%.2f, J+1=%.2f, J+3=%.2f, J+5=%.2f",
        symbol, current_price,
        predictions["1d"]["price"],
        predictions["3d"]["price"],
        predictions["5d"]["price"],
    )

    return {
        "symbol": symbol,
        "current_price": current_price,
        "predictions": predictions,
        "model_age_seconds": round(model_age, 1),
    }
