"""Async wrapper around the synchronous ``yfinance`` library.

Spec authority: master-prompt.md §5 (external integrations) + Step 5
plan (Q1 — yfinance lib via ``asyncio.to_thread``).

Why yfinance and not direct HTTP?
  - Yahoo's quote/chart endpoints are undocumented and change shape
    without notice. ``yfinance`` absorbs that drift (CSRF cookies,
    crumb handshake, header rotation) so we don't have to.
  - We pay for it with: a synchronous IO library + occasional IP rate
    limit + zero SLA. We mitigate by:
      * running every call in a worker thread (``asyncio.to_thread``)
        so the event loop is never blocked,
      * tenacity retries (3 × exponential backoff) on transient errors,
      * a fail-graceful contract: every method returns ``None`` on hard
        failure rather than raising — the service layer turns that into
        verdict INDÉTERMINÉ / ``price_source="unavailable"``.

See docs/YFINANCE_INTEGRATION_NOTES.md for the fallback playbook.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from typing import Any

from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings

logger = logging.getLogger(__name__)


class _RetryableYFError(Exception):
    """Internal marker — wraps transient YFinance failures for tenacity."""


class YFinanceClient:
    """Thin async wrapper around ``yfinance.Ticker``.

    Every public method:
      - runs the blocking yfinance call in a thread,
      - retries up to 3× on transient errors,
      - returns ``None`` on permanent failure (caller handles fallback).

    Two consumers in V1:
      - ``portfolio_service`` — needs ``get_info(symbol)`` for ``regularMarketPrice``.
      - ``valuation_service`` — needs ``get_history(symbol, period="5y")``
        for the multiples-vs-historical method, and ``get_info`` for the
        analyst-target method (``targetMedianPrice`` + ``numberOfAnalystOpinions``).
    """

    def __init__(self, *, timeout_s: float | None = None) -> None:
        self._timeout = timeout_s or settings.YFINANCE_TIMEOUT_S

    # ── Public surface ─────────────────────────────────────────────────────

    async def get_info(self, symbol: str) -> dict[str, Any] | None:
        """Return ``yfinance.Ticker(symbol).info`` or ``None`` on hard failure.

        ``.info`` is a Yahoo quoteSummary aggregate (~150 keys). Keys we
        rely on:
          - ``regularMarketPrice`` / ``currentPrice``  (live last trade)
          - ``targetMedianPrice``, ``targetLowPrice``, ``targetHighPrice``
          - ``numberOfAnalystOpinions``
        Yahoo silently drops keys when data is stale; callers must treat
        every key as optional.
        """
        return await self._with_retry(
            self._fetch_info_sync, symbol, context_label=f"info {symbol}"
        )

    async def get_history(
        self,
        symbol: str,
        *,
        period: str = "5y",
        interval: str = "1mo",
    ) -> list[dict[str, Any]] | None:
        """Return historical OHLC bars as a list of dicts (date + close).

        Each item: ``{"date": "YYYY-MM-DD", "close": float}``. We return a
        plain Python list (not a DataFrame) so the boundary between the
        thread-bounded yfinance code and the asyncio service layer is
        clean. Monthly granularity (default ``1mo``) is enough for the
        spec §4.3.1 method (multiples vs 5y historical medians).
        """
        return await self._with_retry(
            self._fetch_history_sync,
            symbol,
            period=period,
            interval=interval,
            context_label=f"history {symbol} period={period} interval={interval}",
        )

    # ── Sync workers (run in thread) ───────────────────────────────────────

    @staticmethod
    def _fetch_info_sync(symbol: str) -> dict[str, Any] | None:
        import yfinance as yf  # local import to keep import-time cheap in tests

        ticker = yf.Ticker(symbol)
        try:
            info = ticker.info
        except Exception as exc:  # yfinance raises bare Exception subclasses
            raise _RetryableYFError(f"info raised: {exc}") from exc

        if not isinstance(info, dict) or not info:
            # Empty {} or None → Yahoo silently rejected the symbol or
            # rate-limited us. Treat as transient (retry) — a real
            # not-found will keep returning {} and exhaust retries,
            # which the caller turns into None.
            raise _RetryableYFError(f"info empty for {symbol!r}")

        # Some unknown tickers come back with a tiny stub like
        # {"trailingPegRatio": None}. Require at least one price-ish field.
        if not any(
            k in info for k in ("regularMarketPrice", "currentPrice", "previousClose")
        ):
            raise _RetryableYFError(f"info has no price field for {symbol!r}")

        return info

    @staticmethod
    def _fetch_history_sync(
        symbol: str, *, period: str, interval: str
    ) -> list[dict[str, Any]] | None:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        try:
            df = ticker.history(period=period, interval=interval, auto_adjust=False)
        except Exception as exc:
            raise _RetryableYFError(f"history raised: {exc}") from exc

        if df is None or df.empty:
            raise _RetryableYFError(f"history empty for {symbol!r}")

        if "Close" not in df.columns:
            raise _RetryableYFError(f"history missing Close col for {symbol!r}")

        import math

        bars: list[dict[str, Any]] = []
        for idx, close in df["Close"].items():
            if close is None:
                continue
            try:
                close_f = float(close)
            except (TypeError, ValueError):
                continue
            if math.isnan(close_f) or math.isinf(close_f):
                continue
            try:
                d: date = idx.date()
            except AttributeError:
                continue
            bars.append({"date": d.isoformat(), "close": close_f})

        if not bars:
            raise _RetryableYFError(f"history yielded zero usable bars for {symbol!r}")

        return bars

    # ── Retry plumbing ─────────────────────────────────────────────────────

    async def _with_retry(
        self,
        sync_fn,
        *args,
        context_label: str,
        **kwargs,
    ):
        """Run ``sync_fn(*args, **kwargs)`` in a thread, with retries.

        Returns the result on success, or ``None`` after retries are
        exhausted. NEVER raises — Yahoo flakiness is expected, callers
        choose the fallback policy.
        """
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=2.0),
                retry=retry_if_exception_type(_RetryableYFError),
                reraise=True,
            ):
                with attempt:
                    return await asyncio.wait_for(
                        asyncio.to_thread(sync_fn, *args, **kwargs),
                        timeout=self._timeout,
                    )
        except _RetryableYFError as exc:
            logger.warning(
                "yfinance unavailable after retries context=%s err=%s",
                context_label,
                exc,
            )
            return None
        except asyncio.TimeoutError:
            logger.warning(
                "yfinance timeout after %.1fs context=%s",
                self._timeout,
                context_label,
            )
            return None
        except Exception as exc:  # belt-and-suspenders
            logger.error(
                "yfinance unexpected error context=%s err=%s",
                context_label,
                exc,
            )
            return None

        return None  # pragma: no cover


# ── FastAPI dependency provider ─────────────────────────────────────────────

_singleton: YFinanceClient | None = None


def get_yfinance_client() -> YFinanceClient:
    """Lazy singleton — tests override via ``app.dependency_overrides``."""
    global _singleton
    if _singleton is None:
        _singleton = YFinanceClient()
    return _singleton
