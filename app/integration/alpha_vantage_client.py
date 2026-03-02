import httpx

from app.core.config import get_settings
from app.core.logging import logger

BASE_URL = "https://www.alphavantage.co/query"


async def get_overview(symbol: str) -> dict | None:
    """Fetch company overview from Alpha Vantage (optional enrichment)."""
    settings = get_settings()
    if not settings.alpha_vantage_api_key:
        logger.debug("Alpha Vantage key not configured — skipping overview")
        return None

    params = {
        "function": "OVERVIEW",
        "symbol": symbol,
        "apikey": settings.alpha_vantage_api_key,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(BASE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    if "Symbol" not in data:
        logger.warning("Alpha Vantage returned no overview for %s", symbol)
        return None

    return data


async def get_income_statement(symbol: str) -> dict | None:
    """Fetch annual income statement from Alpha Vantage."""
    settings = get_settings()
    if not settings.alpha_vantage_api_key:
        return None

    params = {
        "function": "INCOME_STATEMENT",
        "symbol": symbol,
        "apikey": settings.alpha_vantage_api_key,
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.get(BASE_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

    if "annualReports" not in data:
        return None

    return data
