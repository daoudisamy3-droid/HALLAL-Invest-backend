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

    async def get_calendar(self, symbol: str) -> dict[str, Any] | None:
        """Return earnings + dividend calendar dicts.

        Composes the result from ``Ticker.calendar`` (raw next-event blob)
        + ``Ticker.earnings_history`` (last ~4 quarters EPS estimate vs
        actual) + ``Ticker.dividends`` (timeseries of past distributions).
        Any sub-fetch may individually be missing — the caller treats the
        union; if every field is empty we still return ``None`` so the
        endpoint surfaces ``available=false``.
        """
        return await self._with_retry(
            self._fetch_calendar_sync, symbol, context_label=f"calendar {symbol}"
        )

    async def get_management(self, symbol: str) -> list[dict[str, Any]] | None:
        """Return ``Ticker.info["companyOfficers"]`` — list of officer dicts
        with name, title, age, totalPay, exercisedValue, yearBorn."""
        return await self._with_retry(
            self._fetch_management_sync, symbol, context_label=f"management {symbol}"
        )

    async def get_holders(self, symbol: str) -> dict[str, Any] | None:
        """Return a composite dict: ``major_holders`` (% institutional /
        insider), ``institutional_holders`` (top 10), ``insider_transactions``
        (last ~10). Sub-fetches that fail are simply omitted (the endpoint
        surfaces partial data rather than rolling back to None)."""
        return await self._with_retry(
            self._fetch_holders_sync, symbol, context_label=f"holders {symbol}"
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
    def _fetch_calendar_sync(symbol: str) -> dict[str, Any] | None:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        out: dict[str, Any] = {}

        # 1. next earnings + dividend dates (yfinance .calendar)
        try:
            cal = ticker.calendar
        except Exception as exc:  # noqa: BLE001
            raise _RetryableYFError(f"calendar raised: {exc}") from exc
        if isinstance(cal, dict) and cal:
            # Normalise non-JSON-friendly values (Timestamp, etc.) → ISO strings.
            for k, v in cal.items():
                out[k] = _coerce_jsonable(v)

        # 2. earnings history (EPS estimate vs actual on the last quarters)
        try:
            eh = ticker.earnings_history
        except Exception:
            eh = None
        out["earnings_history"] = _df_to_records(eh)

        # 3. dividends timeseries
        try:
            divs = ticker.dividends
        except Exception:
            divs = None
        out["dividends"] = _series_to_records(divs, value_key="amount")

        # If literally everything is empty, surface a transient None.
        if (
            not cal
            and not out["earnings_history"]
            and not out["dividends"]
        ):
            raise _RetryableYFError(f"calendar yielded nothing for {symbol!r}")

        return out

    @staticmethod
    def _fetch_management_sync(symbol: str) -> list[dict[str, Any]] | None:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        try:
            info = ticker.info
        except Exception as exc:  # noqa: BLE001
            raise _RetryableYFError(f"info raised: {exc}") from exc

        if not isinstance(info, dict):
            raise _RetryableYFError(f"info not a dict for {symbol!r}")

        officers = info.get("companyOfficers")
        if not isinstance(officers, list) or not officers:
            raise _RetryableYFError(f"companyOfficers empty for {symbol!r}")

        normalised: list[dict[str, Any]] = []
        for o in officers:
            if not isinstance(o, dict):
                continue
            normalised.append({
                "name":           o.get("name"),
                "title":           o.get("title"),
                "age":             o.get("age"),
                "year_born":       o.get("yearBorn"),
                "total_pay":       _coerce_jsonable(o.get("totalPay")),
                "exercised_value": _coerce_jsonable(o.get("exercisedValue")),
                "unexercised_value": _coerce_jsonable(o.get("unexercisedValue")),
                "fiscal_year":     o.get("fiscalYear"),
            })
        return normalised

    @staticmethod
    def _fetch_holders_sync(symbol: str) -> dict[str, Any] | None:
        import yfinance as yf

        ticker = yf.Ticker(symbol)
        out: dict[str, Any] = {}

        try:
            major = ticker.major_holders
        except Exception:
            major = None
        out["major_holders"] = _df_to_records(major)

        try:
            inst = ticker.institutional_holders
        except Exception:
            inst = None
        out["institutional_holders"] = _df_to_records(inst)

        try:
            insider = ticker.insider_transactions
        except Exception:
            insider = None
        out["insider_transactions"] = _df_to_records(insider)

        if not out["major_holders"] and not out["institutional_holders"] and not out["insider_transactions"]:
            raise _RetryableYFError(f"holders yielded nothing for {symbol!r}")
        return out

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


# ─── Phase C helpers — JSON normalisers for pandas/Timestamp ────────────────


def _coerce_jsonable(v: Any) -> Any:
    """Convert pandas / numpy / datetime values to JSONable primitives."""
    import math
    from datetime import date, datetime

    if v is None:
        return None
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    # pandas Timestamp / numpy scalars expose isoformat() or item().
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:
            pass
    if hasattr(v, "item"):
        try:
            scalar = v.item()
            if isinstance(scalar, float) and (math.isnan(scalar) or math.isinf(scalar)):
                return None
            return scalar
        except Exception:
            pass
    if isinstance(v, (list, tuple)):
        return [_coerce_jsonable(x) for x in v]
    if isinstance(v, dict):
        return {str(k): _coerce_jsonable(x) for k, x in v.items()}
    return v


def _df_to_records(df: Any) -> list[dict[str, Any]]:
    """Pandas DataFrame → list of JSONable dicts. None / empty → []."""
    if df is None:
        return []
    try:
        if df.empty:
            return []
    except AttributeError:
        return []

    records: list[dict[str, Any]] = []
    try:
        # Reset index so any datetime index becomes a column.
        df = df.reset_index()
        for row in df.to_dict(orient="records"):
            records.append({str(k): _coerce_jsonable(v) for k, v in row.items()})
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance _df_to_records failed err=%s", exc)
        return []
    return records


def _series_to_records(series: Any, *, value_key: str = "value") -> list[dict[str, Any]]:
    """Pandas Series → list of {date, value_key} dicts. None / empty → []."""
    if series is None:
        return []
    try:
        if series.empty:
            return []
    except AttributeError:
        return []

    out: list[dict[str, Any]] = []
    try:
        for idx, value in series.items():
            out.append({
                "date": _coerce_jsonable(idx),
                value_key: _coerce_jsonable(value),
            })
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance _series_to_records failed err=%s", exc)
        return []
    return out
