"""Async HTTP client for SEC EDGAR.

Spec authority: finterminal-spec.md §3.2.2.

  - Base URL          : ``https://data.sec.gov``
  - Ticker map URL    : ``https://www.sec.gov/files/company_tickers.json``
  - Auth              : ``User-Agent`` header required by SEC
  - Rate limit        : 10 req/sec (no daily cap)
  - Coverage          : US-listed equities only

Scope evolution:
  - Step 2: ``get_company_facts`` only (Q7 plan, YAGNI).
  - Step 4: ``get_submissions`` added for the fraud-gate restatement
    signal + SIC code (sector classification). Held in a per-process
    in-memory cache (no DB), refreshed on container restart.

``get_concept`` (single concept history endpoint) is still deferred —
its use-case is more granular than ``get_company_facts`` which already
returns everything we need in a single fetch.

CIK lookup is in-memory (Q2 plan): the ticker→CIK map is fetched once
on first use and held for the life of the process. Process restarts on
Railway are frequent enough to keep the cache "fresh" within the
spec's 7-day TTL.
"""

import asyncio
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
from app.core.exceptions import SECEdgarError

logger = logging.getLogger(__name__)


class _RetryableHTTPError(Exception):
    """Internal marker — wraps transient upstream failures for tenacity."""


class SecEdgarClient:
    """Thin wrapper around httpx.AsyncClient for the SEC EDGAR XBRL API."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        ticker_map_url: str | None = None,
        user_agent: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self._base_url = (base_url or settings.SEC_EDGAR_BASE_URL).rstrip("/")
        self._ticker_map_url = ticker_map_url or settings.SEC_EDGAR_TICKER_MAP_URL
        self._user_agent = user_agent or settings.SEC_EDGAR_USER_AGENT
        self._timeout = httpx.Timeout(timeout_s or settings.SEC_EDGAR_TIMEOUT_S)
        self._headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }
        # CIK lookup cache (per-instance — fresh client = fresh cache for tests)
        self._cik_map: dict[str, tuple[int, str]] | None = None
        self._cik_lock = asyncio.Lock()
        # Submissions cache (per-instance, per-CIK, in-memory).
        # Step 4 fraud-gate Signal 3 needs the filings list; we cache it
        # alongside the SIC code used for sector classification. Process
        # restart on Railway flushes; that's < the spec's 24h TTL.
        self._submissions_cache: dict[int, dict[str, Any]] = {}
        self._submissions_lock = asyncio.Lock()

    # ── CIK lookup ──────────────────────────────────────────────────────────

    async def lookup_cik(self, ticker: str) -> tuple[int, str] | None:
        """Return (CIK, entity_name) for the ticker, or None if not in SEC's universe.

        Raises ``SECEdgarError`` only if the ticker map itself can't be fetched.
        A missing ticker (non-US, unknown) returns ``None`` — that's a normal
        coverage outcome, not an error.
        """
        cik_map = await self._get_cik_map()
        return cik_map.get(ticker.upper().strip())

    async def _get_cik_map(self) -> dict[str, tuple[int, str]]:
        if self._cik_map is not None:
            return self._cik_map
        async with self._cik_lock:
            if self._cik_map is not None:  # double-check after acquiring
                return self._cik_map
            raw = await self._do_http_get(self._ticker_map_url, "ticker_map")
            if raw is None:
                # 404 on the ticker map file is catastrophic — surface as error.
                raise SECEdgarError("SEC EDGAR ticker_map returned 404")
            built: dict[str, tuple[int, str]] = {}
            for entry in raw.values():
                if not isinstance(entry, dict):
                    continue
                ticker_raw = entry.get("ticker")
                cik_raw = entry.get("cik_str")
                if not isinstance(ticker_raw, str) or cik_raw is None:
                    continue
                try:
                    cik = int(cik_raw)
                except (TypeError, ValueError):
                    continue
                title = str(entry.get("title", "") or "")
                built[ticker_raw.upper()] = (cik, title)
            logger.info("sec_edgar ticker_map loaded entries=%d", len(built))
            self._cik_map = built
            return self._cik_map

    # ── Submissions (Step 4 — sector + recent filings) ─────────────────────

    async def get_submissions(self, cik: int) -> dict[str, Any] | None:
        """Fetch the SEC submissions blob for ``cik`` (filings list + metadata).

        Endpoint: ``https://data.sec.gov/submissions/CIK{cik:010d}.json``.

        Returns the parsed JSON dict (contains ``sicCode``,
        ``sicDescription``, ``name``, ``filings.recent`` array, …) or
        ``None`` on 404. Process-cached (no DB) with no TTL — relies on
        Railway container restarts to refresh.

        Used by:
          - sector classifier (``sicCode`` → EXEMPT_SIC_RANGES check)
          - fraud-gate Signal 3 (count of 10-K/A / 10-Q/A / NT filings)
        """
        if cik in self._submissions_cache:
            return self._submissions_cache[cik]
        async with self._submissions_lock:
            if cik in self._submissions_cache:
                return self._submissions_cache[cik]
            url = f"{self._base_url}/submissions/CIK{cik:010d}.json"
            data = await self._do_http_get(url, f"submissions cik={cik}")
            if data is not None:
                self._submissions_cache[cik] = data
            return data

    # ── Company facts ──────────────────────────────────────────────────────

    async def get_company_facts(self, cik: int) -> dict[str, Any] | None:
        """Fetch the XBRL company_facts blob for ``cik``.

        Returns the parsed JSON dict on 200, ``None`` on 404. Raises
        ``SECEdgarError`` on any other failure mode (5xx, timeout,
        network, malformed JSON, exhausted retries). CIK is zero-padded
        to 10 digits per SEC URL convention.
        """
        url = f"{self._base_url}/api/xbrl/companyfacts/CIK{cik:010d}.json"
        return await self._do_http_get(url, f"company_facts cik={cik}")

    # ── HTTP plumbing ──────────────────────────────────────────────────────

    async def _do_http_get(
        self, url: str, context_label: str
    ) -> dict[str, Any] | None:
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=2.0),
                retry=retry_if_exception_type(_RetryableHTTPError),
                reraise=True,
            ):
                with attempt:
                    return await self._issue_request(url, context_label)
        except _RetryableHTTPError as exc:
            raise SECEdgarError(str(exc)) from exc
        except SECEdgarError:
            raise
        except Exception as exc:  # belt-and-suspenders
            raise SECEdgarError(f"unexpected error calling SEC EDGAR: {exc}") from exc

        raise SECEdgarError("retry loop exited without result")  # pragma: no cover

    async def _issue_request(
        self, url: str, context_label: str
    ) -> dict[str, Any] | None:
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(url, headers=self._headers)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.TransportError) as exc:
            logger.warning(
                "sec_edgar transient error context=%s err=%s", context_label, exc
            )
            raise _RetryableHTTPError(f"transport error: {exc}") from exc

        if response.status_code == 404:
            logger.info("sec_edgar 404 context=%s", context_label)
            return None

        if 500 <= response.status_code < 600:
            logger.warning(
                "sec_edgar upstream 5xx context=%s status=%s body=%s",
                context_label,
                response.status_code,
                response.text[:200],
            )
            raise _RetryableHTTPError(f"upstream {response.status_code}")

        if response.status_code >= 400:
            body_preview = response.text[:300]
            logger.error(
                "sec_edgar non-2xx (non-retryable) context=%s status=%s body=%s",
                context_label,
                response.status_code,
                body_preview,
            )
            raise SECEdgarError(
                f"sec_edgar returned {response.status_code}: {body_preview}"
            )

        try:
            data = response.json()
        except ValueError as exc:
            raise SECEdgarError(f"malformed JSON from sec_edgar: {exc}") from exc

        if not isinstance(data, dict):
            raise SECEdgarError(
                f"sec_edgar returned non-object JSON: {type(data).__name__}"
            )

        logger.info(
            "sec_edgar 200 context=%s body_size=%d", context_label, len(response.text)
        )
        return data


# ── FastAPI dependency provider ─────────────────────────────────────────────

_singleton: SecEdgarClient | None = None


def get_sec_edgar_client() -> SecEdgarClient:
    """FastAPI dependency provider — lazy singleton.

    Tests override via ``app.dependency_overrides[get_sec_edgar_client]``
    or by monkey-patching ``_singleton``.
    """
    global _singleton
    if _singleton is None:
        _singleton = SecEdgarClient()
    return _singleton
