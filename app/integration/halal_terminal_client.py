"""Async HTTP client for the Halal Terminal screening API.

Spec authority: finterminal-spec.md §3.2.1.

  - Base URL : `https://api.halalterminal.com`
  - Auth     : `X-API-Key` header
  - Endpoint : `POST /api/screen/{symbol}`
  - Response : JSON with `ratios`, `methodologies`, `as_of_date`, …

Per master-prompt §5.3 the AAOIFI gate has NO fallback — when this
client fails to obtain a result, downstream code must surface a
blocking ERROR verdict, not approximate or skip.

Retry policy: 2 attempts (total 3 calls) with short exponential
backoff. Token-metered upstream, so we keep retries minimal and only
on transient failures (5xx, timeout, network). 4xx are not retried.
"""

import logging
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.core.config import settings
from app.core.exceptions import HalalTerminalError

logger = logging.getLogger(__name__)


class _RetryableHTTPError(Exception):
    """Internal marker — wraps transient upstream failures for tenacity."""


class HalalTerminalClient:
    """Thin wrapper around httpx.AsyncClient for the screening endpoint."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._base_url = (base_url or settings.HALAL_TERMINAL_BASE_URL).rstrip("/")
        self._api_key = api_key or settings.HALAL_TERMINAL_API_KEY
        self._timeout = httpx.Timeout(timeout_s or settings.HALAL_TERMINAL_TIMEOUT_S)
        self._headers = {
            "X-API-Key": self._api_key,
            "Accept": "application/json",
        }

    async def screen(self, symbol: str) -> dict[str, Any] | None:
        """Call POST /api/screen/{symbol}.

        Returns the parsed JSON body on success. Returns ``None`` if the
        provider answers 404 (symbol not covered). Raises
        :class:`HalalTerminalError` on any other failure mode (5xx,
        timeout, network, malformed JSON, exhausted retries).
        """
        url = f"{self._base_url}/api/screen/{symbol}"

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=2.0),
                retry=retry_if_exception_type(_RetryableHTTPError),
                reraise=True,
            ):
                with attempt:
                    return await self._do_call(url, symbol)
        except _RetryableHTTPError as exc:
            raise HalalTerminalError(str(exc)) from exc
        except HalalTerminalError:
            raise
        except Exception as exc:  # belt-and-suspenders
            raise HalalTerminalError(f"unexpected error calling Halal Terminal: {exc}") from exc

        # tenacity reraise=True means we never reach this line — silence type checker.
        raise HalalTerminalError("retry loop exited without result")  # pragma: no cover

    async def _do_call(self, url: str, symbol: str) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(url, headers=self._headers)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
            logger.warning("halal_terminal transient error symbol=%s err=%s", symbol, exc)
            raise _RetryableHTTPError(f"transport error: {exc}") from exc

        if response.status_code == 404:
            logger.info("halal_terminal coverage miss symbol=%s", symbol)
            return None

        if 500 <= response.status_code < 600:
            logger.warning(
                "halal_terminal upstream 5xx symbol=%s status=%s body=%s",
                symbol,
                response.status_code,
                response.text[:200],
            )
            raise _RetryableHTTPError(f"upstream {response.status_code}")

        if response.status_code >= 400:
            # 401 / 403 / 422 — auth or contract issue, non-retryable
            raise HalalTerminalError(
                f"halal_terminal returned {response.status_code}: {response.text[:200]}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise HalalTerminalError(f"malformed JSON from halal_terminal: {exc}") from exc

        if not isinstance(data, dict):
            raise HalalTerminalError(
                f"halal_terminal returned non-object JSON: {type(data).__name__}"
            )

        return data


_singleton: HalalTerminalClient | None = None


def get_halal_terminal_client() -> HalalTerminalClient:
    """FastAPI dependency provider.

    Returns a module-level singleton lazily instantiated on first call,
    so tests can monkey-patch `app.integration.halal_terminal_client._singleton`
    or use FastAPI's `app.dependency_overrides[get_halal_terminal_client]`
    to inject a fake.
    """
    global _singleton
    if _singleton is None:
        _singleton = HalalTerminalClient()
    return _singleton
