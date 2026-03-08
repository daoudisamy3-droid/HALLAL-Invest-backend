"""
Data Loader — Alpaca Markets REST API → pandas DataFrame.

Fetches daily OHLCV bars for a given symbol.
Retry logic: up to 3 retries with exponential backoff on transient errors.
After fetch, prices are recalibrated against the YFinance live price
to correct any split/ADR discrepancies between data sources.
"""

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
import pandas as pd

from app.core.config import get_settings
from app.core.logging import logger

# ── Constants ────────────────────────────────────────────────────

_BARS_ENDPOINT = "/v2/stocks/{symbol}/bars"
_MAX_RETRIES = 3
_BASE_DELAY = 2  # seconds


async def fetch_ohlcv(
    symbol: str,
    limit: int = 1000,
    timeframe: str = "1Day",
    end: Optional[datetime] = None,
) -> pd.DataFrame:
    """
    Fetch daily OHLCV bars from Alpaca Markets.

    Args:
        symbol:    Ticker symbol (e.g. "AAPL").
        limit:     Max number of bars (Alpaca caps at 10 000).
        timeframe: Bar size — "1Day", "1Hour", etc.
        end:       End date (defaults to now UTC).

    Returns:
        DataFrame with columns: [timestamp, open, high, low, close, volume]
        sorted by timestamp ascending.

    Raises:
        ValueError:  If Alpaca API keys are missing.
        RuntimeError: If all retries are exhausted.
    """
    settings = get_settings()

    if not settings.alpaca_api_key or not settings.alpaca_api_secret:
        raise ValueError(
            "ALPACA_API_KEY and ALPACA_API_SECRET must be set in environment variables."
        )

    if end is None:
        end = datetime.now(timezone.utc)
    start = end - timedelta(days=limit * 2)  # generous window for trading days

    url = f"{settings.alpaca_base_url}{_BARS_ENDPOINT.format(symbol=symbol.upper())}"
    params = {
        "timeframe": timeframe,
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": limit,
        "adjustment": "all",
        "feed": "iex",
        "sort": "asc",
    }
    headers = {
        "APCA-API-KEY-ID": settings.alpaca_api_key,
        "APCA-API-SECRET-KEY": settings.alpaca_api_secret,
        "Accept": "application/json",
    }

    last_error: Optional[Exception] = None

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(url, params=params, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                bars = data.get("bars") or []
                if not bars:
                    raise RuntimeError(
                        f"Alpaca returned 0 bars for '{symbol}'. "
                        "The symbol may be invalid or have no trading history."
                    )
                df = _bars_to_dataframe(bars, symbol)
                return await _recalibrate_prices(df, symbol)

            # Auth errors — no point retrying
            if resp.status_code in (401, 403):
                raise ValueError(
                    f"Alpaca authentication failed (HTTP {resp.status_code}). "
                    "Check your ALPACA_API_KEY and ALPACA_API_SECRET."
                )

            # Symbol not found
            if resp.status_code == 404:
                raise ValueError(f"Symbol '{symbol}' not found on Alpaca.")

            # Rate limit or server error — retry
            last_error = RuntimeError(
                f"Alpaca API returned HTTP {resp.status_code}: {resp.text[:200]}"
            )
            logger.warning(
                "Alpaca attempt %d/%d for %s failed: HTTP %d",
                attempt, _MAX_RETRIES, symbol, resp.status_code,
            )

        except (httpx.ConnectError, httpx.ReadTimeout, httpx.ConnectTimeout) as exc:
            last_error = exc
            logger.warning(
                "Alpaca attempt %d/%d for %s — network error: %s",
                attempt, _MAX_RETRIES, symbol, exc,
            )

        # Exponential backoff before retry
        if attempt < _MAX_RETRIES:
            delay = _BASE_DELAY * (2 ** (attempt - 1))
            logger.info("Retrying in %ds...", delay)
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"Alpaca API: all {_MAX_RETRIES} attempts exhausted for '{symbol}'. "
        f"Last error: {last_error}"
    )


def _bars_to_dataframe(bars: list[dict], symbol: str) -> pd.DataFrame:
    """Convert Alpaca bar dicts to a clean DataFrame."""
    df = pd.DataFrame(bars)

    # Alpaca returns: t (timestamp), o, h, l, c, v, n, vw
    rename_map = {
        "t": "timestamp",
        "o": "open",
        "h": "high",
        "l": "low",
        "c": "close",
        "v": "volume",
    }
    df = df.rename(columns=rename_map)

    # Keep only the columns we need
    keep = [c for c in ["timestamp", "open", "high", "low", "close", "volume"] if c in df.columns]
    df = df[keep]

    # Parse timestamps and sort
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    logger.info(
        "Alpaca: loaded %d bars for %s (%s → %s)",
        len(df), symbol,
        df["timestamp"].iloc[0].strftime("%Y-%m-%d"),
        df["timestamp"].iloc[-1].strftime("%Y-%m-%d"),
    )

    return df


# ── YFinance recalibration ────────────────────────────────────────

_SCALING_THRESHOLD = 0.005  # 0.5 %
_PRICE_COLUMNS = ["open", "high", "low", "close"]


async def _recalibrate_prices(df: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """
    Align Alpaca historical prices to the YFinance live price.

    If the last close from Alpaca differs from the YFinance price by more
    than 0.5 %, all OHLC columns are scaled proportionally.  This corrects
    split / ADR / adjustment mismatches between data providers.
    """
    from app.integration import yfinance_client  # local import to avoid circular deps

    try:
        price_data = await yfinance_client.get_fast_price(symbol)
        yf_price = price_data.get("price") or price_data.get("current_price")
        if yf_price is None:
            logger.warning("YFinance returned no price for %s — skipping recalibration", symbol)
            return df
        yf_price = float(yf_price)
    except Exception as exc:
        logger.warning("YFinance price fetch failed for %s (%s) — skipping recalibration", symbol, exc)
        return df

    alpaca_last_close = float(df["close"].iloc[-1])

    if alpaca_last_close == 0:
        logger.warning("Alpaca last close is 0 for %s — skipping recalibration", symbol)
        return df

    scaling_factor = yf_price / alpaca_last_close
    deviation = abs(scaling_factor - 1.0)

    if deviation <= _SCALING_THRESHOLD:
        logger.info(
            "No recalibration needed for %s (deviation=%.4f%%, threshold=%.1f%%)",
            symbol, deviation * 100, _SCALING_THRESHOLD * 100,
        )
        return df

    # Apply scaling to all price columns
    logger.info(
        "Recalibrage appliqué pour %s: facteur %.6f "
        "(YF=%.2f, Alpaca=%.2f, écart=%.2f%%)",
        symbol, scaling_factor, yf_price, alpaca_last_close, deviation * 100,
    )

    df = df.copy()
    for col in _PRICE_COLUMNS:
        if col in df.columns:
            df[col] = df[col] * scaling_factor

    return df
