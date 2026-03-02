from fastapi import APIRouter

from app.api.v1.endpoints.ticker import router as ticker_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(ticker_router, tags=["Ticker Analysis"])
