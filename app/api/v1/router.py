from fastapi import APIRouter

from app.api.v1.endpoints.ticker import router as ticker_router
from app.api.v1.endpoints.prediction import router as prediction_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(ticker_router, tags=["Ticker Analysis"])
api_router.include_router(prediction_router, tags=["ML Predictions"])
