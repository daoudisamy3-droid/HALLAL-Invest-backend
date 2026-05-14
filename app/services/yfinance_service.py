"""YFinance service — orchestration layer with DB-backed TTL cache.

Step 5 (master-prompt mapping). Sits between the raw
``app/integration/yfinance_client.py`` (which knows how to call yfinance
in a thread + retry) and the consumers (``portfolio_service``,
``valuation_service``).

Two cached endpoints:
  - ``get_info(ticker)``     — TTL = ``settings.YFINANCE_INFO_TTL_HOURS`` (1h)
  - ``get_history(ticker)``  — TTL = ``settings.YFINANCE_HISTORY_TTL_HOURS`` (24h)

Both return ``None`` on hard failure. Cache misses with a None client
result are NOT persisted — Yahoo flakiness is transient.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.integration.yfinance_client import YFinanceClient
from app.models.yfinance_cache import YFinanceCache

logger = logging.getLogger(__name__)


_KIND_INFO = "info"
_KIND_HISTORY = "history"
_KIND_CALENDAR = "calendar"
_KIND_MANAGEMENT = "management"
_KIND_HOLDERS = "holders"

# Phase C TTLs (longer than info/history since these change rarely):
#   - calendar 6h (next earnings date moves only on guidance changes)
#   - management 24h (officers list changes once or twice a year)
#   - holders 24h (institutional 13F is quarterly anyway)
_CALENDAR_TTL_HOURS = 6
_MANAGEMENT_TTL_HOURS = 24
_HOLDERS_TTL_HOURS = 24


# ─── Public API ─────────────────────────────────────────────────────────────


async def get_info(
    ticker: str,
    db: AsyncSession,
    client: YFinanceClient,
) -> dict[str, Any] | None:
    """Return the cached or freshly-fetched .info dict, or None on failure."""
    tk = ticker.strip().upper()

    cached = await _read_cache(
        db, tk, _KIND_INFO, params="", ttl_hours=settings.YFINANCE_INFO_TTL_HOURS
    )
    if cached is not None:
        logger.info("yfinance_service cache HIT kind=info ticker=%s", tk)
        return cached if isinstance(cached, dict) else None

    payload = await client.get_info(tk)
    if payload is None:
        logger.warning("yfinance_service MISS+fail kind=info ticker=%s", tk)
        return None

    await _persist(db, tk, _KIND_INFO, params="", payload=payload)
    logger.info("yfinance_service MISS+fresh kind=info ticker=%s", tk)
    return payload


async def get_history(
    ticker: str,
    db: AsyncSession,
    client: YFinanceClient,
    *,
    period: str = "5y",
    interval: str = "1mo",
) -> list[dict[str, Any]] | None:
    """Return cached or freshly-fetched OHLC bars, or None on failure."""
    tk = ticker.strip().upper()
    params = f"period={period}/interval={interval}"

    cached = await _read_cache(
        db, tk, _KIND_HISTORY,
        params=params,
        ttl_hours=settings.YFINANCE_HISTORY_TTL_HOURS,
    )
    if cached is not None:
        logger.info(
            "yfinance_service cache HIT kind=history ticker=%s params=%s",
            tk, params,
        )
        return cached if isinstance(cached, list) else None

    payload = await client.get_history(tk, period=period, interval=interval)
    if payload is None:
        logger.warning(
            "yfinance_service MISS+fail kind=history ticker=%s params=%s",
            tk, params,
        )
        return None

    await _persist(db, tk, _KIND_HISTORY, params=params, payload=payload)
    logger.info(
        "yfinance_service MISS+fresh kind=history ticker=%s params=%s bars=%d",
        tk, params, len(payload),
    )
    return payload


# ─── Cache layer ────────────────────────────────────────────────────────────


async def _read_cache(
    db: AsyncSession,
    ticker: str,
    payload_kind: str,
    *,
    params: str,
    ttl_hours: int,
) -> dict[str, Any] | list[Any] | None:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=ttl_hours)
    stmt = (
        select(YFinanceCache)
        .where(YFinanceCache.ticker == ticker)
        .where(YFinanceCache.payload_kind == payload_kind)
        .where(YFinanceCache.params == params)
        .where(YFinanceCache.fetched_at >= cutoff)
        .order_by(YFinanceCache.fetched_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return None
    return row.payload_json


async def _persist(
    db: AsyncSession,
    ticker: str,
    payload_kind: str,
    *,
    params: str,
    payload: dict[str, Any] | list[Any],
) -> None:
    try:
        row = YFinanceCache(
            id=uuid.uuid4(),
            ticker=ticker,
            payload_kind=payload_kind,
            params=params,
            fetched_at=datetime.now(tz=timezone.utc),
            payload_json=_sanitize_for_jsonb(payload),
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        logger.warning(
            "yfinance_service persist failed ticker=%s kind=%s err=%s",
            ticker, payload_kind, exc,
        )
        await db.rollback()


def _sanitize_for_jsonb(payload: Any) -> Any:
    """Drop non-JSON-serialisable values (NaN, datetime, etc.) from a dict/list.

    Yahoo's ``.info`` occasionally yields ``float('nan')`` or pandas
    timestamps that asyncpg rejects when binding JSONB. Recursively
    coerce: NaN/inf → None, datetime → isoformat string.
    """
    import math
    from datetime import date, datetime

    if isinstance(payload, dict):
        return {k: _sanitize_for_jsonb(v) for k, v in payload.items()}
    if isinstance(payload, list):
        return [_sanitize_for_jsonb(v) for v in payload]
    if isinstance(payload, float):
        if math.isnan(payload) or math.isinf(payload):
            return None
        return payload
    if isinstance(payload, (datetime, date)):
        return payload.isoformat()
    return payload


# ─── Phase C — Calendar / Management / Holders ──────────────────────────────


async def get_calendar(
    ticker: str, db: AsyncSession, client: YFinanceClient,
) -> dict[str, Any] | None:
    """Cached calendar (next earnings + dividend events + history)."""
    tk = ticker.strip().upper()
    cached = await _read_cache(
        db, tk, _KIND_CALENDAR, params="", ttl_hours=_CALENDAR_TTL_HOURS,
    )
    if cached is not None:
        logger.info("yfinance_service cache HIT kind=calendar ticker=%s", tk)
        return cached if isinstance(cached, dict) else None
    payload = await client.get_calendar(tk)
    if payload is None:
        logger.warning("yfinance_service MISS+fail kind=calendar ticker=%s", tk)
        return None
    await _persist(db, tk, _KIND_CALENDAR, params="", payload=payload)
    return payload


async def get_management(
    ticker: str, db: AsyncSession, client: YFinanceClient,
) -> list[dict[str, Any]] | None:
    """Cached list of company officers."""
    tk = ticker.strip().upper()
    cached = await _read_cache(
        db, tk, _KIND_MANAGEMENT, params="", ttl_hours=_MANAGEMENT_TTL_HOURS,
    )
    if cached is not None:
        logger.info("yfinance_service cache HIT kind=management ticker=%s", tk)
        return cached if isinstance(cached, list) else None
    payload = await client.get_management(tk)
    if payload is None:
        logger.warning("yfinance_service MISS+fail kind=management ticker=%s", tk)
        return None
    await _persist(db, tk, _KIND_MANAGEMENT, params="", payload=payload)
    return payload


async def get_holders(
    ticker: str, db: AsyncSession, client: YFinanceClient,
) -> dict[str, Any] | None:
    """Cached major + institutional + insider holdings."""
    tk = ticker.strip().upper()
    cached = await _read_cache(
        db, tk, _KIND_HOLDERS, params="", ttl_hours=_HOLDERS_TTL_HOURS,
    )
    if cached is not None:
        logger.info("yfinance_service cache HIT kind=holders ticker=%s", tk)
        return cached if isinstance(cached, dict) else None
    payload = await client.get_holders(tk)
    if payload is None:
        logger.warning("yfinance_service MISS+fail kind=holders ticker=%s", tk)
        return None
    await _persist(db, tk, _KIND_HOLDERS, params="", payload=payload)
    return payload
