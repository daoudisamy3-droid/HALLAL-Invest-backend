"""GET /api/v1/shariah/{symbol} — Halal Terminal AAOIFI gate (§3.2.1, §5).

The endpoint always returns 200 with a `ShariahReport` body. The
business verdict — PASS / FAIL / ERROR / NOT_COVERED — lives inside
the payload so downstream UIs can render uniformly regardless of
upstream availability. HTTP non-2xx is reserved for auth failures
(401 via `verify_api_key` at router level) and unhandled crashes.

Auth: covered by the router-level `verify_api_key` dependency. No
extra logic here.
"""

from fastapi import APIRouter, Depends, Path

from app.core.database import AsyncSessionLocal
from app.integration.halal_terminal_client import (
    HalalTerminalClient,
    get_halal_terminal_client,
)
from app.schemas.shariah import ShariahReport
from app.services.shariah_service import screen_with_personal_thresholds

router = APIRouter(prefix="/shariah", tags=["shariah"])


async def _db_session():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/{symbol}", response_model=ShariahReport)
async def shariah_screen(
    symbol: str = Path(..., min_length=1, max_length=20),
    db=Depends(_db_session),
    client: HalalTerminalClient = Depends(get_halal_terminal_client),
) -> ShariahReport:
    return await screen_with_personal_thresholds(symbol, db, client)
