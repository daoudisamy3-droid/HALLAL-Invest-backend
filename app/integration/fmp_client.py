from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import logger

FMP_BASE = "https://financialmodelingprep.com/api/v3"


async def _fmp_get(path: str, params: Optional[dict] = None) -> Any:
    """Generic FMP GET request with API key injection."""
    settings = get_settings()
    if not settings.fmp_api_key:
        raise RuntimeError("FMP_API_KEY is required for AAOIFI audit")

    req_params = {"apikey": settings.fmp_api_key}
    if params:
        req_params.update(params)

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(f"{FMP_BASE}/{path}", params=req_params)
        if resp.status_code == 403:
            logger.warning("FMP 403 on %s — endpoint blocked or limit reached", path)
            return None
        resp.raise_for_status()
        return resp.json()


async def get_balance_sheet(symbol: str, limit: int = 1) -> Optional[list[dict]]:
    """Fetch annual balance sheet statements from FMP."""
    logger.info("FMP: fetching balance sheet for %s", symbol)
    data = await _fmp_get(f"balance-sheet-statement/{symbol}", {"limit": str(limit)})
    if not data or not isinstance(data, list):
        return None
    return data


async def get_income_statement(symbol: str, limit: int = 1) -> Optional[list[dict]]:
    """Fetch annual income statements from FMP."""
    logger.info("FMP: fetching income statement for %s", symbol)
    data = await _fmp_get(f"income-statement/{symbol}", {"limit": str(limit)})
    if not data or not isinstance(data, list):
        return None
    return data


async def get_revenue_segmentation(symbol: str) -> Optional[dict]:
    """Fetch product revenue segmentation from FMP. Returns None on 403."""
    logger.info("FMP: fetching revenue segmentation for %s", symbol)
    data = await _fmp_get(f"revenue-product-segmentation/{symbol}", {"structure": "flat"})
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    return data[0] if isinstance(data[0], dict) else None
