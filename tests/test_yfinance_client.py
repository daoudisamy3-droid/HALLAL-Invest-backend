"""Fragility tests for ``app/integration/yfinance_client.py``.

The yfinance library is mocked at module level (the client uses
``import yfinance as yf`` inside its sync workers). Each test monkeypatches
``yfinance.Ticker`` to a controlled fake so we exercise the retry +
timeout + fail-graceful paths without hitting Yahoo.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.integration.yfinance_client import YFinanceClient


# ─── helpers ────────────────────────────────────────────────────────────────


class _FakeTicker:
    def __init__(self, *, info=None, history=None, raise_n: int = 0) -> None:
        self._info = info
        self._history = history
        self._raise_n = raise_n
        self.calls = 0

    @property
    def info(self) -> Any:
        self.calls += 1
        if self.calls <= self._raise_n:
            raise RuntimeError(f"yahoo flaky call #{self.calls}")
        return self._info

    def history(self, **_kwargs):  # noqa: ARG002
        self.calls += 1
        if self.calls <= self._raise_n:
            raise RuntimeError(f"yahoo history flaky #{self.calls}")
        return self._history


def _patch_yf(monkeypatch, ticker_instance) -> None:
    """Replace ``yfinance.Ticker(...)`` with a lambda returning our fake."""
    fake_module = SimpleNamespace(Ticker=lambda _symbol: ticker_instance)
    import sys
    monkeypatch.setitem(sys.modules, "yfinance", fake_module)


# ─── get_info ───────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_get_info_success_returns_dict(monkeypatch) -> None:
    fake = _FakeTicker(info={"regularMarketPrice": 235.5, "symbol": "AAPL"})
    _patch_yf(monkeypatch, fake)

    client = YFinanceClient()
    out = await client.get_info("AAPL")
    assert out is not None
    assert out["regularMarketPrice"] == 235.5
    assert fake.calls == 1


@pytest.mark.unit
async def test_get_info_retries_on_transient_then_succeeds(monkeypatch) -> None:
    """First 2 calls raise; 3rd succeeds — must surface success after retry."""
    fake = _FakeTicker(
        info={"regularMarketPrice": 99.0},
        raise_n=2,
    )
    _patch_yf(monkeypatch, fake)

    client = YFinanceClient()
    out = await client.get_info("AAPL")
    assert out is not None
    assert out["regularMarketPrice"] == 99.0
    assert fake.calls == 3  # 2 failures + 1 success


@pytest.mark.unit
async def test_get_info_exhausts_retries_returns_none(monkeypatch) -> None:
    """3+ consecutive failures → graceful None (NEVER raise)."""
    fake = _FakeTicker(info={"regularMarketPrice": 100.0}, raise_n=10)
    _patch_yf(monkeypatch, fake)

    client = YFinanceClient()
    out = await client.get_info("AAPL")
    assert out is None
    assert fake.calls == 3  # 3 attempts, all failed


@pytest.mark.unit
async def test_get_info_empty_dict_treated_as_transient(monkeypatch) -> None:
    """Yahoo returning {} (rate limit) is transient → retried then None."""
    fake = _FakeTicker(info={})
    _patch_yf(monkeypatch, fake)

    client = YFinanceClient()
    out = await client.get_info("ZZZZ")
    assert out is None
    assert fake.calls == 3


@pytest.mark.unit
async def test_get_info_stub_without_price_field_returns_none(monkeypatch) -> None:
    """Yahoo stub like {'trailingPegRatio': None} → unusable, falls through."""
    fake = _FakeTicker(info={"trailingPegRatio": None})
    _patch_yf(monkeypatch, fake)

    client = YFinanceClient()
    out = await client.get_info("ZZZZ")
    assert out is None


@pytest.mark.unit
async def test_get_info_timeout_returns_none(monkeypatch) -> None:
    """A blocking call > timeout_s must be cancelled and return None."""
    class _SlowTicker:
        @property
        def info(self):
            import time
            time.sleep(2.0)
            return {"regularMarketPrice": 1.0}

        def history(self, **_kw):
            import time
            time.sleep(2.0)
            return None

    _patch_yf(monkeypatch, _SlowTicker())

    client = YFinanceClient(timeout_s=0.1)
    out = await client.get_info("AAPL")
    assert out is None


# ─── get_history ────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_get_history_success_returns_bars(monkeypatch) -> None:
    """Build a tiny synthetic DataFrame-like object."""
    import pandas as pd  # yfinance dep — fine to import here

    df = pd.DataFrame(
        {"Close": [100.0, 110.0, 120.0]},
        index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-31"]),
    )
    fake = _FakeTicker(history=df)
    _patch_yf(monkeypatch, fake)

    client = YFinanceClient()
    bars = await client.get_history("AAPL", period="5y", interval="1mo")
    assert bars is not None
    assert len(bars) == 3
    assert bars[0] == {"date": "2024-01-31", "close": 100.0}
    assert bars[2] == {"date": "2024-03-31", "close": 120.0}


@pytest.mark.unit
async def test_get_history_empty_df_returns_none(monkeypatch) -> None:
    import pandas as pd
    fake = _FakeTicker(history=pd.DataFrame())
    _patch_yf(monkeypatch, fake)

    client = YFinanceClient()
    bars = await client.get_history("ZZZZ")
    assert bars is None


@pytest.mark.unit
async def test_get_history_missing_close_column_returns_none(monkeypatch) -> None:
    import pandas as pd
    df = pd.DataFrame({"Open": [1.0]}, index=pd.to_datetime(["2024-01-31"]))
    fake = _FakeTicker(history=df)
    _patch_yf(monkeypatch, fake)

    client = YFinanceClient()
    bars = await client.get_history("AAPL")
    assert bars is None


@pytest.mark.unit
async def test_get_history_drops_nan_closes(monkeypatch) -> None:
    """NaN Close rows must be silently skipped, not crash."""
    import math
    import pandas as pd
    df = pd.DataFrame(
        {"Close": [100.0, math.nan, 120.0]},
        index=pd.to_datetime(["2024-01-31", "2024-02-29", "2024-03-31"]),
    )
    fake = _FakeTicker(history=df)
    _patch_yf(monkeypatch, fake)

    client = YFinanceClient()
    bars = await client.get_history("AAPL")
    assert bars is not None
    assert len(bars) == 2  # NaN row dropped
    closes = [b["close"] for b in bars]
    assert closes == [100.0, 120.0]
