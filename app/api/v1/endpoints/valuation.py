"""GET /api/v1/valuation/{symbol} — Valuation snapshot (§4.3.7).

Always returns 200; the verdict (OUI / OUI_NEUTRE / NON / INDÉTERMINÉ)
lives in the body for uniform downstream handling (consistent with the
shariah / investissable / financials endpoints).
"""

from fastapi import APIRouter, Depends, Path

from app.core.database import AsyncSessionLocal
from app.integration.sec_edgar_client import SecEdgarClient, get_sec_edgar_client
from app.schemas.valuation import ValuationReport
from app.services.valuation_service import compute_valuation


router = APIRouter(prefix="/valuation", tags=["valuation"])


async def _db_session():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/{symbol}", response_model=ValuationReport)
async def valuation(
    symbol: str = Path(..., min_length=1, max_length=20),
    db=Depends(_db_session),
    sec_client: SecEdgarClient = Depends(get_sec_edgar_client),
) -> ValuationReport:
    return await compute_valuation(symbol, db, sec_client)
