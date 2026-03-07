from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import logger

FMP_BASE = "https://financialmodelingprep.com/api/v3"


def _normalize_symbol(symbol: str) -> str:
    """
    Strip exchange suffixes that yfinance adds but FMP doesn't use.
    e.g. 'AAPL.L' → 'AAPL', '7203.T' stays '7203.T' (FMP uses it for Tokyo).
    FMP uses dots for non-US exchanges, but not the same ones as yfinance.
    For safety, only strip known yfinance-only suffixes.
    """
    # yfinance suffixes that don't exist in FMP
    yf_only_suffixes = {
        ".AX", ".TO", ".SA", ".NS", ".BO", ".SI", ".HK",
        ".KS", ".KQ", ".TW", ".TWO", ".JK", ".ME",
    }
    for suffix in yf_only_suffixes:
        if symbol.endswith(suffix):
            stripped = symbol[: -len(suffix)]
            logger.info("FMP: normalized %s → %s (stripped %s)", symbol, stripped, suffix)
            return stripped
    return symbol


async def _fmp_get(path: str, params: Optional[dict] = None) -> Any:
    """Generic FMP GET request with API key injection and full debug logging."""
    settings = get_settings()
    if not settings.fmp_api_key:
        logger.error("FMP_API_KEY is empty — set it in .env")
        raise RuntimeError("FMP_API_KEY is required for AAOIFI audit")

    key_preview = settings.fmp_api_key[:6] + "..." if len(settings.fmp_api_key) > 6 else "***"

    req_params = {"apikey": settings.fmp_api_key}
    if params:
        req_params.update(params)

    url = f"{FMP_BASE}/{path}"
    # Log URL without full key
    log_params = {k: (v if k != "apikey" else key_preview) for k, v in req_params.items()}
    logger.info("FMP REQUEST: GET %s params=%s", url, log_params)

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(url, params=req_params)

    logger.info("FMP RESPONSE: %s → status=%d, length=%d", path, resp.status_code, len(resp.content))

    if resp.status_code == 403:
        logger.warning("FMP 403 on %s — endpoint blocked or rate limit reached", path)
        return None

    if resp.status_code == 401:
        logger.error("FMP 401 on %s — invalid API key (key starts with %s)", path, key_preview)
        return None

    resp.raise_for_status()

    data = resp.json()

    # FMP returns error objects like {"Error Message": "..."} for invalid requests
    if isinstance(data, dict) and "Error Message" in data:
        logger.warning("FMP error for %s: %s", path, data["Error Message"])
        return None

    # Log a preview of what we got
    if isinstance(data, list):
        logger.info("FMP DATA: %s → list of %d items", path, len(data))
        if data:
            keys = list(data[0].keys())[:8] if isinstance(data[0], dict) else []
            logger.info("FMP DATA: first item keys (preview): %s", keys)
    elif isinstance(data, dict):
        logger.info("FMP DATA: %s → dict with keys: %s", path, list(data.keys())[:8])
    else:
        logger.info("FMP DATA: %s → type=%s", path, type(data).__name__)

    return data


async def get_balance_sheet(symbol: str, limit: int = 1) -> Optional[list[dict]]:
    """Fetch annual balance sheet statements from FMP."""
    symbol = _normalize_symbol(symbol)
    logger.info("FMP: fetching balance sheet for %s", symbol)
    data = await _fmp_get(f"balance-sheet-statement/{symbol}", {"limit": str(limit)})
    if not data or not isinstance(data, list) or len(data) == 0:
        logger.warning("FMP: balance sheet empty or invalid for %s (got: %s)", symbol, type(data).__name__)
        return None
    return data


async def get_income_statement(symbol: str, limit: int = 1) -> Optional[list[dict]]:
    """Fetch annual income statements from FMP."""
    symbol = _normalize_symbol(symbol)
    logger.info("FMP: fetching income statement for %s", symbol)
    data = await _fmp_get(f"income-statement/{symbol}", {"limit": str(limit)})
    if not data or not isinstance(data, list) or len(data) == 0:
        logger.warning("FMP: income statement empty or invalid for %s (got: %s)", symbol, type(data).__name__)
        return None
    return data


async def get_revenue_segmentation(symbol: str) -> Optional[dict]:
    """Fetch product revenue segmentation from FMP. Returns None on 403."""
    symbol = _normalize_symbol(symbol)
    logger.info("FMP: fetching revenue segmentation for %s", symbol)
    data = await _fmp_get(f"revenue-product-segmentation/{symbol}", {"structure": "flat"})
    if not data or not isinstance(data, list) or len(data) == 0:
        logger.warning("FMP: revenue segmentation empty or invalid for %s", symbol)
        return None
    # FMP returns [{date: {segment: value, ...}}, ...]
    first = data[0]
    if isinstance(first, dict):
        # Flatten: if structure is {date_str: {segments}}, extract the inner dict
        for key, val in first.items():
            if isinstance(val, dict):
                logger.info("FMP: revenue segmentation found %d segments for %s", len(val), symbol)
                return val
        return first
    return None
