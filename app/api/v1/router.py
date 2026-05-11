from fastapi import APIRouter, Depends
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
