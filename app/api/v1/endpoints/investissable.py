"""GET /api/v1/investissable/{symbol} — INVESTISSABLE verdict (§4.2.4).

Always returns 200; the verdict (OUI / NON / INCERTAIN) lives in the
body for uniform downstream handling (cohérent avec /shariah et
/financials).
"""

from fastapi import APIRouter, Depends, Path

from app.core.database import AsyncSessionLocal
from app.integration.halal_terminal_client import (
    HalalTerminalClient,
    get_halal_terminal_client,
)
from app.integration.sec_edgar_client import SecEdgarClient, get_sec_edgar_client
from app.schemas.investissable import InvestissableReport
from app.services.investissable_service import compute_investissable


router = APIRouter(prefix="/investissable", tags=["investissable"])


async def _db_session():
    async with AsyncSessionLocal() as session:
        yield session


@router.get("/{symbol}", response_model=InvestissableReport)
async def investissable(
    symbol: str = Path(..., min_length=1, max_length=20),
    db=Depends(_db_session),
    halal_client: HalalTerminalClient = Depends(get_halal_terminal_client),
    sec_client: SecEdgarClient = Depends(get_sec_edgar_client),
) -> InvestissableReport:
    return await compute_investissable(symbol, db, halal_client, sec_client)
