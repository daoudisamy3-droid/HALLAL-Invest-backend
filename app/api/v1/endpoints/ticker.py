from fastapi import APIRouter, HTTPException, Depends

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.integration import yfinance_client
from app.models.schemas import TickerInfo, PriceResponse

router = APIRouter()


@router.get(
    "/ticker/{symbol}/price",
    response_model=PriceResponse,
    summary="Lightweight price snapshot",
    description="Returns current price, change, and change percentage.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_ticker_price(symbol: str) -> PriceResponse:
    symbol = symbol.upper().strip()
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    try:
        data = await yfinance_client.get_fast_price(symbol)
    except Exception as exc:
        logger.error("Price fetch failed for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch price for '{symbol}'. Verify the symbol is correct.",
        )

    return PriceResponse(**data)


@router.get(
    "/ticker/{symbol}",
    response_model=TickerInfo,
    summary="Validate ticker and return basic info",
    description="Checks that a ticker symbol exists and returns its basic metadata.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_ticker(symbol: str) -> TickerInfo:
    symbol = symbol.upper().strip()
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    try:
        info = await yfinance_client.get_ticker_info(symbol)
    except Exception as exc:
        logger.error("Ticker lookup failed for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=404,
            detail=f"Could not find ticker '{symbol}'. Verify the symbol is correct.",
        )

    website = info.get("website")
    domain = None
    if website:
        from urllib.parse import urlparse
        host = urlparse(website).hostname or ""
        domain = host[4:] if host.startswith("www.") else host or None

    return TickerInfo(
        symbol=symbol,
        name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        currency=info.get("currency"),
        website=domain,
    )
