"""GET /api/v1/synthesis/{symbol} — 3-layer aggregate verdict (§4.4).

Always returns 200; the overall_verdict (INVESTABLE / NOT_INVESTABLE /
REQUIRES_REVIEW / BLOCKED) and per-layer status live in the body. The
endpoint never raises 5xx — per-layer failures are surfaced as
``available=false`` + ``errors[]`` (Step 6 plan validated).

Step 7.1: no shared ``db`` session — ``compute_synthesis`` opens one
session per parallel branch internally (cf. service module docstring).
"""

from fastapi import APIRouter, Depends, Path

from app.integration.halal_terminal_client import (
    HalalTerminalClient,
    get_halal_terminal_client,
)
from app.integration.sec_edgar_client import SecEdgarClient, get_sec_edgar_client
from app.integration.yfinance_client import YFinanceClient, get_yfinance_client
from app.schemas.synthesis import SynthesisReport
from app.services.synthesis_service import compute_synthesis


router = APIRouter(prefix="/synthesis", tags=["synthesis"])


@router.get("/{symbol}", response_model=SynthesisReport)
async def synthesis(
    symbol: str = Path(..., min_length=1, max_length=20),
    halal_client: HalalTerminalClient = Depends(get_halal_terminal_client),
    sec_client: SecEdgarClient = Depends(get_sec_edgar_client),
    yfinance: YFinanceClient = Depends(get_yfinance_client),
) -> SynthesisReport:
    return await compute_synthesis(symbol, halal_client, sec_client, yfinance)
