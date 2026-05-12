"""TTL cache tests for ``app/services/yfinance_service.py``.

These exercise the read-through + write-behind cache logic against a
real Postgres (function-scoped engine fixture from ``conftest.py``). The
YFinance client itself is mocked end-to-end.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.config import settings
from app.models.yfinance_cache import YFinanceCache
from app.services import yfinance_service as ys


# ─── get_info ───────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_info_cache_miss_calls_client_and_persists(db) -> None:
    """First call: client invoked, payload persisted to yfinance_cache."""
    client = AsyncMock()
    client.get_info.return_value = {"regularMarketPrice": 235.5}

    out = await ys.get_info("AAPL", db, client)
    assert out == {"regularMarketPrice": 235.5}
    client.get_info.assert_awaited_once_with("AAPL")

    rows = (await db.execute(select(YFinanceCache))).scalars().all()
    assert len(rows) == 1
    assert rows[0].ticker == "AAPL"
    assert rows[0].payload_kind == "info"
    assert rows[0].payload_json == {"regularMarketPrice": 235.5}


@pytest.mark.integration
async def test_get_info_cache_hit_does_not_call_client(db) -> None:
    """Second call within TTL must NOT re-invoke the client."""
    client = AsyncMock()
    client.get_info.return_value = {"regularMarketPrice": 100.0}

    await ys.get_info("AAPL", db, client)
    client.get_info.reset_mock()

    out = await ys.get_info("AAPL", db, client)
    assert out == {"regularMarketPrice": 100.0}
    client.get_info.assert_not_awaited()


@pytest.mark.integration
async def test_get_info_stale_row_falls_through_to_client(db) -> None:
    """A pre-existing row older than TTL must NOT be returned."""
    # Seed a stale row (fetched_at > TTL hours ago).
    stale = YFinanceCache(
        ticker="AAPL",
        payload_kind="info",
        params="",
        fetched_at=datetime.now(tz=timezone.utc)
        - timedelta(hours=settings.YFINANCE_INFO_TTL_HOURS + 1),
        payload_json={"regularMarketPrice": 50.0},  # old
    )
    db.add(stale)
    await db.commit()

    client = AsyncMock()
    client.get_info.return_value = {"regularMarketPrice": 250.0}  # fresh
    out = await ys.get_info("AAPL", db, client)
    assert out == {"regularMarketPrice": 250.0}  # fresh, not stale
    client.get_info.assert_awaited_once()


@pytest.mark.integration
async def test_get_info_client_returns_none_does_not_persist(db) -> None:
    """A None result must not poison the cache."""
    client = AsyncMock()
    client.get_info.return_value = None

    out = await ys.get_info("ZZZZ", db, client)
    assert out is None
    rows = (await db.execute(select(YFinanceCache))).scalars().all()
    assert rows == []


@pytest.mark.integration
async def test_get_info_sanitises_nan_floats(db) -> None:
    """yfinance occasionally returns NaN; persisting must not crash."""
    import math
    client = AsyncMock()
    client.get_info.return_value = {
        "regularMarketPrice": 100.0,
        "forwardPE": math.nan,  # would fail JSONB binding raw
    }

    out = await ys.get_info("AAPL", db, client)
    # Service returns the original payload (sanitisation happens on persist).
    assert out is not None
    rows = (await db.execute(select(YFinanceCache))).scalars().all()
    assert len(rows) == 1
    assert rows[0].payload_json["forwardPE"] is None  # NaN → None
    assert rows[0].payload_json["regularMarketPrice"] == 100.0


# ─── get_history ────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_get_history_cache_separates_by_params(db) -> None:
    """Different (period, interval) tuples are independent cache slots."""
    client = AsyncMock()
    client.get_history.side_effect = [
        [{"date": "2024-01-31", "close": 100.0}],  # period=5y
        [{"date": "2024-12-31", "close": 200.0}],  # period=1y
    ]

    out_5y = await ys.get_history("AAPL", db, client, period="5y", interval="1mo")
    out_1y = await ys.get_history("AAPL", db, client, period="1y", interval="1mo")

    assert out_5y == [{"date": "2024-01-31", "close": 100.0}]
    assert out_1y == [{"date": "2024-12-31", "close": 200.0}]
    assert client.get_history.await_count == 2

    # Both rows distinct in DB
    rows = (await db.execute(select(YFinanceCache))).scalars().all()
    assert len(rows) == 2
    assert {r.params for r in rows} == {
        "period=5y/interval=1mo",
        "period=1y/interval=1mo",
    }


@pytest.mark.integration
async def test_get_history_cache_hit(db) -> None:
    client = AsyncMock()
    client.get_history.return_value = [{"date": "2024-01-31", "close": 100.0}]

    await ys.get_history("AAPL", db, client, period="5y", interval="1mo")
    client.get_history.reset_mock()

    out = await ys.get_history("AAPL", db, client, period="5y", interval="1mo")
    assert out == [{"date": "2024-01-31", "close": 100.0}]
    client.get_history.assert_not_awaited()
