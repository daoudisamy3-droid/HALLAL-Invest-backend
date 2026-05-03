from fastapi import APIRouter

from app.api.v1.endpoints.ticker import router as ticker_router
from app.api.v1.endpoints.prediction import router as prediction_router
from app.api.v1.endpoints.macro import router as macro_router
from app.api.v1.endpoints.map import router as map_router
from app.api.v1.endpoints.analyze import router as analyze_router
from app.api.v1.endpoints.risk import router as risk_router
from app.api.v1.endpoints.calendar import router as calendar_router
from app.api.v1.endpoints.rels import router as rels_router
from app.api.v1.endpoints.score import router as score_router
from app.api.v1.endpoints.portfolio import router as portfolio_router
from app.api.v1.endpoints.plan import router as plan_router
from app.api.v1.endpoints.macro_dashboard import router as macro_dashboard_router

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(ticker_router, tags=["Ticker Analysis"])
api_router.include_router(prediction_router, tags=["ML Predictions"])
api_router.include_router(macro_router, tags=["Macro Economy"])
api_router.include_router(map_router, tags=["Map Infrastructure"])
api_router.include_router(analyze_router, tags=["Analyze Engine"])
api_router.include_router(risk_router, tags=["Risk Intelligence"])
api_router.include_router(calendar_router, tags=["Calendar"])
api_router.include_router(rels_router, tags=["RELS"])
api_router.include_router(score_router, tags=["Score Engine"])
api_router.include_router(portfolio_router, tags=["Portfolio"])
api_router.include_router(plan_router, tags=["Plan"])
api_router.include_router(macro_dashboard_router, tags=["Macro Dashboard"])
