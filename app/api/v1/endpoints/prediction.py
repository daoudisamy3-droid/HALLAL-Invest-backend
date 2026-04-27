"""
Prediction API — ML-powered price forecasting endpoints.

Routes:
    GET /predict/{symbol}  → predicted price for J+1 with directional scoring
    GET /metrics/{symbol}  → MAE from cross-validation per horizon
"""

from fastapi import APIRouter, Depends, HTTPException

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.ml.predictor import get_prediction, _model_cache


router = APIRouter()


@router.get(
    "/predict/{symbol}",
    summary="ML Price Prediction",
    description=(
        "Returns the current price, 10-day volatility, and predicted price for J+1 "
        "using a RandomForest model trained on OHLCV features. "
        "Includes directional score (UP/DOWN). Models are cached for 24h per symbol."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def predict(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    try:
        result = await get_prediction(symbol)
    except Exception as exc:
        logger.error("Prediction failed for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Prediction failed for '{symbol}': {exc}",
        )

    return result


@router.get(
    "/metrics/{symbol}",
    summary="Model Evaluation Metrics",
    description=(
        "Returns the mean absolute error (MAE) from TimeSeriesSplit "
        "cross-validation for the J+1 prediction horizon. "
        "Requires models to have been trained at least once."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def metrics(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    entry = _model_cache.get(symbol)
    if entry is None:
        # Trigger training to populate metrics
        try:
            await get_prediction(symbol)
            entry = _model_cache.get(symbol)
        except Exception as exc:
            logger.error("Metrics fetch failed for %s: %s", symbol, exc)
            raise HTTPException(
                status_code=502,
                detail=f"Could not train models for '{symbol}': {exc}",
            )

    payload = entry["payload"]

    return {
        "symbol": symbol,
        "metrics": payload["metrics"],
        "training_samples": payload["training_samples"],
        "n_splits": payload["n_splits"],
        "feature_columns": payload["feature_columns"],
    }
