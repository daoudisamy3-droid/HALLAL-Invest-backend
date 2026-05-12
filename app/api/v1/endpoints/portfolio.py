"""Portfolio endpoints (Step 3 part 1).

Five routes under ``/api/v1/portfolio`` (auth via router-level
``verify_api_key``):

    POST   /transactions          → create a transaction (auto-creates position)
    GET    /transactions          → list all transactions, chronological
    DELETE /transactions/{tx_id}  → delete a transaction (cascade position if last)
    GET    /positions             → list positions with derived fields
    GET    /summary               → aggregate snapshot (USD only in V1)
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.database import AsyncSessionLocal
from app.schemas.portfolio import (
    PortfolioSummary,
    PositionRead,
    TransactionCreate,
    TransactionRead,
)
from app.services import portfolio_service

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


async def _db_session():
    async with AsyncSessionLocal() as session:
        yield session


@router.post(
    "/transactions",
    response_model=TransactionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_transaction(
    payload: TransactionCreate,
    db=Depends(_db_session),
) -> TransactionRead:
    try:
        return await portfolio_service.create_transaction(db, payload)
    except portfolio_service.UnsupportedCurrencyError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except portfolio_service.OversellError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/transactions", response_model=list[TransactionRead])
async def list_transactions(db=Depends(_db_session)) -> list[TransactionRead]:
    return await portfolio_service.list_transactions(db)


@router.delete(
    "/transactions/{tx_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_transaction(
    tx_id: UUID,
    db=Depends(_db_session),
) -> None:
    try:
        await portfolio_service.delete_transaction(db, tx_id)
    except portfolio_service.TransactionNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="transaction not found")


@router.get("/positions", response_model=list[PositionRead])
async def list_positions(db=Depends(_db_session)) -> list[PositionRead]:
    return await portfolio_service.list_positions(db)


@router.get("/summary", response_model=PortfolioSummary)
async def get_summary(db=Depends(_db_session)) -> PortfolioSummary:
    return await portfolio_service.get_summary(db)
