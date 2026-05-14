from fastapi import APIRouter, Depends

from app.api.v1.endpoints import financials as financials_endpoint
from app.api.v1.endpoints import investissable as investissable_endpoint
from app.api.v1.endpoints import portfolio as portfolio_endpoint
from app.api.v1.endpoints import shariah as shariah_endpoint
from app.api.v1.endpoints import synthesis as synthesis_endpoint
from app.api.v1.endpoints import valuation as valuation_endpoint
from app.api.v1.endpoints import yfinance_tabs as yftabs_endpoint
from app.core.security import verify_api_key

# IMPORTANT: FastAPI serves /openapi.json and /docs at the app root (main.py),
# NOT under this router's /api/v1 prefix. They are therefore NOT covered by
# verify_api_key. This is intentional — the schema must remain public so that
# npm run types:generate (curl raw GitHub) and tooling can fetch it without auth.
# Do not move /openapi.json behind this router.
api_router = APIRouter(
    prefix="/api/v1",
    dependencies=[Depends(verify_api_key)],
)

# ── Ping (smoke / health-check for authenticated callers) ─────────────────────
@api_router.get("/ping", tags=["meta"])
async def ping() -> dict[str, bool]:
    return {"pong": True}


# ── Feature routers ───────────────────────────────────────────────────────────
api_router.include_router(shariah_endpoint.router)        # GET /api/v1/shariah/{symbol}
api_router.include_router(financials_endpoint.router)     # GET /api/v1/financials/{ticker}
api_router.include_router(portfolio_endpoint.router)      # /api/v1/portfolio/*
api_router.include_router(investissable_endpoint.router)  # GET /api/v1/investissable/{symbol}
api_router.include_router(valuation_endpoint.router)      # GET /api/v1/valuation/{symbol}
api_router.include_router(synthesis_endpoint.router)      # GET /api/v1/synthesis/{symbol}
api_router.include_router(yftabs_endpoint.calendar_router)   # GET /api/v1/calendar/{symbol}
api_router.include_router(yftabs_endpoint.management_router) # GET /api/v1/management/{symbol}
api_router.include_router(yftabs_endpoint.holders_router)    # GET /api/v1/holders/{symbol}
