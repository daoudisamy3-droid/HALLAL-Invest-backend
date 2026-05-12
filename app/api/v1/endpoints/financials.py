"""GET /api/v1/financials/{ticker} — SEC EDGAR snapshot endpoint (§3.2.2).

Returns the latest annual financials extracted from SEC EDGAR for a
US-listed ticker. Always 200 OK — the business verdict (AVAILABLE /
NOT_COVERED / ERROR) lives in the payload, matching the pattern
established by the shariah endpoint for uniform downstream handling.

Auth: covered by the router-level `verify_api_key` dependency.
"""

from fastapi import APIRouter, Depends, Path

from app.core.database import AsyncSessionLocal
from app.integration.sec_edgar_client import SecEdgarClient, get_sec_edgar_client
from app.schemas.financials import FinancialsSnapshot
from app.services.financials_service import get_financials

router = APIRouter(prefix="/financials", tags=["financials"])


async def _db_session():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/{ticker}", response_model=FinancialsSnapshot)
async def financials_snapshot(
    ticker: str = Path(..., min_length=1, max_length=20),
    db=Depends(_db_session),
    client: SecEdgarClient = Depends(get_sec_edgar_client),
) -> FinancialsSnapshot:
    return await get_financials(ticker, db, client)
