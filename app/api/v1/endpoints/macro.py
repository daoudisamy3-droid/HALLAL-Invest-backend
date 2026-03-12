from fastapi import APIRouter, Depends

from app.core.security import rate_limit_dependency
from app.services.macro_service import get_world_indices, get_comparison_data

router = APIRouter()


@router.get(
    "/macro/indices",
    summary="World market indices",
    description="S&P 500, Nasdaq 100, CAC 40, DAX, Nikkei 225 — price, change %, and market open status.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def world_indices():
    return await get_world_indices()


@router.get(
    "/macro/comparison",
    summary="Base-0 intraday comparison (LineChart-ready)",
    description="Flat array of {time, US500, NDX100, FR40, DE40, JP225} with % change from day open.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def comparison():
    return await get_comparison_data()
