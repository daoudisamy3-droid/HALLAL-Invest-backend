import os
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import logger

FMP_BASE = "https://financialmodelingprep.com/api/v3"


def _get_fmp_key() -> str:
    """
    Get FMP API key with double safety net:
    1. pydantic-settings (reads .env + env vars)
    2. Direct os.getenv fallback (bypasses lru_cache issues on Railway)
    """
    key = get_settings().fmp_api_key
    if not key:
        key = os.getenv("FMP_API_KEY", "")
        if key:
            logger.info("FMP: key loaded via os.getenv fallback (pydantic-settings returned empty)")
    return key


def _normalize_symbol(symbol: str) -> str:
    """Strip exchange suffixes that yfinance adds but FMP doesn't use."""
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
    """Generic FMP GET request with full debug logging."""
    key = _get_fmp_key()
    if not key:
        logger.error("FMP_API_KEY is empty — set it in Railway env vars or .env file")
        return None

    req_params = {"apikey": key}
    if params:
        req_params.update(params)

    url = f"{FMP_BASE}/{path}"
    log_params = {k: (v if k != "apikey" else "***") for k, v in req_params.items()}
    logger.info("FMP REQUEST: GET %s params=%s", url, log_params)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=req_params)
    except httpx.RequestError as exc:
        logger.error("FMP NETWORK ERROR on %s: %s", path, exc)
        return None

    body_preview = resp.text[:500]
    logger.info(
        "FMP RESPONSE: %s → status=%d length=%d body_preview=%s",
        path, resp.status_code, len(resp.content), body_preview,
    )

    if resp.status_code == 403:
        logger.warning("FMP 403 on %s — rate limit or plan restriction", path)
        return None

    if resp.status_code == 401:
        logger.error("FMP 401 on %s — invalid API key", path)
        return None

    if resp.status_code != 200:
        logger.error("FMP unexpected status %d on %s: %s", resp.status_code, path, body_preview)
        return None

    try:
        data = resp.json()
    except Exception:
        logger.error("FMP JSON parse error on %s: %s", path, body_preview)
        return None

    # FMP error objects: {"Error Message": "..."}
    if isinstance(data, dict) and ("Error Message" in data or "error" in data):
        msg = data.get("Error Message") or data.get("error")
        logger.warning("FMP API error for %s: %s", path, msg)
        return None

    if isinstance(data, list):
        logger.info("FMP DATA: %s → %d items", path, len(data))
    else:
        logger.info("FMP DATA: %s → type=%s", path, type(data).__name__)

    return data


async def get_balance_sheet(symbol: str, limit: int = 1) -> Optional[list[dict]]:
    """Fetch annual balance sheet statements from FMP."""
    symbol = _normalize_symbol(symbol)
    data = await _fmp_get(f"balance-sheet-statement/{symbol}", {"limit": str(limit)})
    if not data or not isinstance(data, list) or len(data) == 0:
        logger.warning("FMP balance sheet returned nothing for %s", symbol)
        return None
    logger.info("FMP balance sheet OK for %s — date=%s", symbol, data[0].get("date"))
    return data


async def get_income_statement(symbol: str, limit: int = 1) -> Optional[list[dict]]:
    """Fetch annual income statements from FMP."""
    symbol = _normalize_symbol(symbol)
    data = await _fmp_get(f"income-statement/{symbol}", {"limit": str(limit)})
    if not data or not isinstance(data, list) or len(data) == 0:
        logger.warning("FMP income statement returned nothing for %s", symbol)
        return None
    logger.info("FMP income statement OK for %s — date=%s", symbol, data[0].get("date"))
    return data


async def get_company_profile(symbol: str) -> Optional[dict]:
    """Fetch company profile (description, sector, industry, CEO, etc.) from FMP."""
    symbol = _normalize_symbol(symbol)
    data = await _fmp_get(f"profile/{symbol}")
    if not data or not isinstance(data, list) or len(data) == 0:
        logger.warning("FMP company profile returned nothing for %s", symbol)
        return None
    logger.info("FMP company profile OK for %s", symbol)
    return data[0]


async def get_revenue_geo_segmentation(symbol: str) -> Optional[dict]:
    """Fetch geographic revenue segmentation from FMP."""
    symbol = _normalize_symbol(symbol)
    data = await _fmp_get(f"revenue-geographic-segmentation/{symbol}", {"structure": "flat"})
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    first = data[0]
    if isinstance(first, dict):
        for _key, val in first.items():
            if isinstance(val, dict):
                logger.info("FMP geo segmentation: %d regions for %s", len(val), symbol)
                return val
        return first
    return None


async def get_revenue_segmentation(symbol: str) -> Optional[dict]:
    """Fetch product revenue segmentation from FMP. Returns None on 403."""
    symbol = _normalize_symbol(symbol)
    data = await _fmp_get(f"revenue-product-segmentation/{symbol}", {"structure": "flat"})
    if not data or not isinstance(data, list) or len(data) == 0:
        return None
    first = data[0]
    if isinstance(first, dict):
        for _key, val in first.items():
            if isinstance(val, dict):
                logger.info("FMP revenue segmentation: %d segments for %s", len(val), symbol)
                return val
        return first
    return None
