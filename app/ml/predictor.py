"""
Prediction Service — Cached XGBoost inference with 24h TTL.

Manages an in-memory cache of trained (XGBClassifier + StandardScaler) per symbol.
On cache miss (or TTL expiry), triggers a full training pipeline.
Fundamentals from /ticker/{symbol} are merged into features before inference.

Lazy J-1 verification:
  On each call, the previous day's stored prediction is compared against
  the actual close (fetched via Alpaca OHLCV).  Directional accuracy
  (UP/DOWN) is tracked per symbol in the shared cache.
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from app.api.v1.endpoints.ticker import get_ticker
from app.core.cache import cache_get, cache_set
from app.core.logging import logger
from app.integration import yfinance_client
from app.ml.data_loader import fetch_ohlcv
from app.ml.features import build_features, FEATURE_COLUMNS
from app.ml.trainer import train_models


# ── In-memory model cache ────────────────────────────────────────
# {symbol: {"trained_at": float, "payload": train_models() result}}
_model_cache: dict[str, dict[str, Any]] = {}

_CACHE_TTL = 86_400  # 24 hours in seconds

# Symbols currently being lazily verified (prevents concurrent double-checks)
_verification_in_progress: set[str] = set()

_PRED_NAMESPACE = "pred"
_ACCURACY_NAMESPACE = "accuracy"
_PRED_TTL = 86_400 * 2      # keep stored predictions for 2 days
_ACCURACY_TTL = 86_400 * 30  # accuracy stats kept 30 days


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


async def _get_close_for_date(symbol: str, target_date: str) -> float | None:
    """Return the closing price for target_date (YYYY-MM-DD) from recent OHLCV bars."""
    try:
        df = await fetch_ohlcv(symbol, limit=10)
        df["date_str"] = df["timestamp"].dt.strftime("%Y-%m-%d")
        row = df[df["date_str"] == target_date]
        if row.empty:
            return None
        return float(row["close"].iloc[-1])
    except Exception as exc:
        logger.warning("Could not fetch close for %s on %s: %s", symbol, target_date, exc)
        return None


async def _verify_yesterday_prediction(symbol: str) -> None:
    """
    Lazy verification of the previous day's directional prediction.

    Fetches the actual close for yesterday, compares it to the stored
    direction (UP/DOWN relative to price_at_prediction), and updates
    the accuracy counters in the shared cache.  No-op if already
    verified, not yet stored, or market was closed.
    """
    if symbol in _verification_in_progress:
        return

    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    cache_key = f"{symbol}:{yesterday}"

    old_pred = cache_get(_PRED_NAMESPACE, cache_key)
    if old_pred is None or old_pred.get("verified"):
        return

    _verification_in_progress.add(symbol)
    try:
        actual_close = await _get_close_for_date(symbol, yesterday)
        if actual_close is None:
            logger.info(
                "No actual close found for %s on %s — skipping verification",
                symbol, yesterday,
            )
            return

        is_up_actual = actual_close > old_pred["price_at_prediction"]
        is_up_pred = old_pred["direction"] == "UP"
        correct = is_up_actual == is_up_pred

        # Update accuracy stats (atomic read-modify-write via cache)
        acc = cache_get(_ACCURACY_NAMESPACE, symbol) or {"total_tries": 0, "success_count": 0}
        acc["total_tries"] += 1
        if correct:
            acc["success_count"] += 1
        cache_set(_ACCURACY_NAMESPACE, symbol, acc, ttl=_ACCURACY_TTL)

        # Mark prediction as verified
        old_pred.update({
            "verified": True,
            "actual_close": actual_close,
            "correct": correct,
        })
        cache_set(_PRED_NAMESPACE, cache_key, old_pred, ttl=_PRED_TTL)

        logger.info(
            "Verification %s on %s: predicted=%s actual=%.2f correct=%s "
            "(accuracy: %d/%d)",
            symbol, yesterday, old_pred["direction"],
            actual_close, correct,
            acc["success_count"], acc["total_tries"],
        )
    finally:
        _verification_in_progress.discard(symbol)


async def get_prediction(symbol: str) -> dict[str, Any]:
    """
    Return J+1 price prediction with directional confidence scoring.

    Flow:
        1. Lazy-verify yesterday's prediction (accuracy tracking).
        2. If models for `symbol` are cached and < 24h old → reuse.
           Otherwise → call train_models(symbol) and cache result.
        3. Fetch fundamentals from /ticker/{symbol} (silent fallback to None).
        4. Fetch latest OHLCV data, build features (with fundamentals).
        5. Scale last row with fitted StandardScaler, run XGBClassifier.
        6. Derive predicted price from confidence-adjusted median return.
        7. Persist today's prediction for tomorrow's verification.

    Args:
        symbol: Ticker symbol (e.g. "AAPL").

    Returns:
        {
            "symbol": str,
            "current_price": float,
            "volatility_10d": float,
            "predictions": {
                "1d": {
                    "price": float,
                    "pct": float,
                    "direction": "UP"|"DOWN",
                    "confidence": float,  # raw proba(UP), 0–1
                },
            },
            "model_age_seconds": float,
        }

    Raises:
        RuntimeError: If data fetch or prediction fails.
    """
    symbol = symbol.upper().strip()

    # ── Step 1: Lazy verification of yesterday ────────────────────
    await _verify_yesterday_prediction(symbol)

    # ── Step 2: Ensure models are trained and cached ──────────────
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
    model = payload["model"]    # XGBClassifier
    scaler = payload["scaler"]  # StandardScaler
    model_age = time.time() - cache_entry["trained_at"]

    # ── Step 3: Fetch fundamentals (silent fallback) ──────────────
    try:
        ticker_data = await get_ticker(symbol)
        fundamentals = ticker_data.model_dump()
    except Exception:
        fundamentals = None

    # ── Step 4: Build features with fundamentals ──────────────────
    df_raw = await fetch_ohlcv(symbol, limit=100)
    df_feat = build_features(df_raw, fundamentals=fundamentals)
    volatility_10d = round(float(df_feat["volatility_10d"].iloc[-1]), 6)

    # Use YFinance as the authoritative current price (matches frontend header)
    try:
        yf_data = await yfinance_client.get_fast_price(symbol)
        current_price = float(yf_data.get("price") or yf_data.get("current_price"))
    except Exception as exc:
        logger.warning(
            "YFinance price unavailable for %s (%s), falling back to Alpaca last close",
            symbol, exc,
        )
        current_price = float(df_feat["close"].iloc[-1])

    # ── Step 5: Scale and infer ───────────────────────────────────
    last_row = df_feat[FEATURE_COLUMNS].iloc[[-1]]
    last_row_scaled = scaler.transform(last_row)

    # Probability of UP (class 1)
    proba = float(model.predict_proba(last_row_scaled)[0][1])
    direction = "UP" if proba >= 0.5 else "DOWN"

    # ── Step 6: Confidence-adjusted price target ──────────────────
    # Apply historical median return weighted by model confidence
    median_return = float(df_feat["return_1d"].median())
    signed_return = median_return if direction == "UP" else -abs(median_return)
    confidence_adjusted = signed_return * proba
    predicted_price = round(current_price * (1 + confidence_adjusted), 2)
    pct = round(confidence_adjusted * 100, 3)

    predictions = {
        "1d": {
            "price": predicted_price,
            "pct": pct,
            "direction": direction,
            "confidence": round(proba, 4),
        }
    }

    # ── Step 7: Persist today's prediction for future verification ─
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    pred_1d = predictions["1d"]

    if cache_get(_PRED_NAMESPACE, f"{symbol}:{today}") is None:
        cache_set(
            _PRED_NAMESPACE,
            f"{symbol}:{today}",
            {
                "direction": pred_1d["direction"],
                "price_at_prediction": current_price,
                "predicted_price": pred_1d["price"],
                "pct": pred_1d["pct"],
                "confidence": pred_1d["confidence"],
                "verified": False,
                "actual_close": None,
                "correct": None,
            },
            ttl=_PRED_TTL,
        )
        logger.info(
            "Stored today's prediction for %s: direction=%s confidence=%.3f price_ref=%.2f",
            symbol, pred_1d["direction"], proba, current_price,
        )

    logger.info(
        "Prediction for %s: price=%.2f J+1=%.2f direction=%s confidence=%.3f",
        symbol, current_price, pred_1d["price"], pred_1d["direction"], proba,
    )

    return {
        "symbol": symbol,
        "current_price": current_price,
        "volatility_10d": volatility_10d,
        "predictions": predictions,
        "model_age_seconds": round(model_age, 1),
    }
