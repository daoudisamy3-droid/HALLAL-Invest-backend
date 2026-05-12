"""Unit tests for app/integration/sec_edgar_client.py (respx mocks).

No live SEC EDGAR access — see test_sec_edgar_live.py for that.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.exceptions import SECEdgarError
from app.integration.sec_edgar_client import SecEdgarClient


DATA_BASE = "https://data.sec.gov"
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"


def _client() -> SecEdgarClient:
    """Fresh client = fresh CIK cache (per-instance singleton)."""
    return SecEdgarClient(
        base_url=DATA_BASE,
        ticker_map_url=TICKER_MAP_URL,
        user_agent="FinTerminal Tests tests@example.com",
        timeout_s=2.0,
    )


def _ticker_map_response() -> dict:
    return {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
        "1": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
        "2": {"cik_str": 1018724, "ticker": "AMZN", "title": "AMAZON COM INC"},
    }


# ─── lookup_cik ─────────────────────────────────────────────────────────────


@pytest.mark.unit
async def test_lookup_cik_returns_cik_and_title_on_match() -> None:
    client = _client()
    with respx.mock() as mock:
        mock.get(TICKER_MAP_URL).mock(
            return_value=httpx.Response(200, json=_ticker_map_response())
        )
        result = await client.lookup_cik("AAPL")

    assert result is not None
    cik, title = result
    assert cik == 320193
    assert title == "Apple Inc."


@pytest.mark.unit
async def test_lookup_cik_returns_none_on_unknown_ticker() -> None:
    client = _client()
    with respx.mock() as mock:
        mock.get(TICKER_MAP_URL).mock(
            return_value=httpx.Response(200, json=_ticker_map_response())
        )
        result = await client.lookup_cik("ZZZBIDON")

    assert result is None


@pytest.mark.unit
async def test_lookup_cik_caches_map_across_calls() -> None:
    client = _client()
    with respx.mock() as mock:
        route = mock.get(TICKER_MAP_URL).mock(
            return_value=httpx.Response(200, json=_ticker_map_response())
        )
        await client.lookup_cik("AAPL")
        await client.lookup_cik("MSFT")
        await client.lookup_cik("AMZN")

    assert route.call_count == 1, "ticker_map must be fetched only once"


@pytest.mark.unit
async def test_lookup_cik_is_case_insensitive() -> None:
    client = _client()
    with respx.mock() as mock:
        mock.get(TICKER_MAP_URL).mock(
            return_value=httpx.Response(200, json=_ticker_map_response())
        )
        result = await client.lookup_cik("aapl")  # lowercase

    assert result is not None
    assert result[0] == 320193


@pytest.mark.unit
async def test_lookup_cik_raises_on_ticker_map_404() -> None:
    client = _client()
    with respx.mock() as mock:
        mock.get(TICKER_MAP_URL).mock(return_value=httpx.Response(404))
        with pytest.raises(SECEdgarError, match="ticker_map returned 404"):
            await client.lookup_cik("AAPL")


# ─── get_company_facts ──────────────────────────────────────────────────────


def _facts_url(cik: int) -> str:
    return f"{DATA_BASE}/api/xbrl/companyfacts/CIK{cik:010d}.json"


@pytest.mark.unit
async def test_get_company_facts_200_returns_dict_with_padded_cik_url() -> None:
    client = _client()
    expected_url = f"{DATA_BASE}/api/xbrl/companyfacts/CIK0000320193.json"
    with respx.mock() as mock:
        route = mock.get(expected_url).mock(
            return_value=httpx.Response(
                200,
                json={"cik": 320193, "entityName": "Apple Inc.", "facts": {"us-gaap": {}}},
            )
        )
        result = await client.get_company_facts(320193)

    assert result is not None
    assert result["entityName"] == "Apple Inc."
    assert route.call_count == 1
    assert str(route.calls.last.request.url) == expected_url


@pytest.mark.unit
async def test_get_company_facts_404_returns_none() -> None:
    client = _client()
    with respx.mock() as mock:
        mock.get(_facts_url(999)).mock(return_value=httpx.Response(404))
        result = await client.get_company_facts(999)

    assert result is None


@pytest.mark.unit
async def test_get_company_facts_retries_on_500_then_fails() -> None:
    client = _client()
    with respx.mock() as mock:
        route = mock.get(_facts_url(320193)).mock(
            return_value=httpx.Response(500, text="boom")
        )
        with pytest.raises(SECEdgarError):
            await client.get_company_facts(320193)

    assert route.call_count == 3, "Expected 1 initial call + 2 retries on 5xx"


@pytest.mark.unit
async def test_get_company_facts_retries_on_timeout() -> None:
    client = _client()
    with respx.mock() as mock:
        route = mock.get(_facts_url(320193)).mock(
            side_effect=httpx.TimeoutException("slow upstream")
        )
        with pytest.raises(SECEdgarError):
            await client.get_company_facts(320193)

    assert route.call_count == 3


@pytest.mark.unit
async def test_get_company_facts_does_not_retry_401() -> None:
    client = _client()
    with respx.mock() as mock:
        route = mock.get(_facts_url(320193)).mock(return_value=httpx.Response(401))
        with pytest.raises(SECEdgarError, match="401"):
            await client.get_company_facts(320193)

    assert route.call_count == 1, "401 is non-retryable"


@pytest.mark.unit
async def test_get_company_facts_recovers_after_transient_500() -> None:
    client = _client()
    with respx.mock() as mock:
        route = mock.get(_facts_url(320193)).mock(
            side_effect=[
                httpx.Response(500, text="transient"),
                httpx.Response(200, json={"entityName": "Apple Inc.", "facts": {}}),
            ]
        )
        result = await client.get_company_facts(320193)

    assert result is not None
    assert route.call_count == 2


@pytest.mark.unit
async def test_get_company_facts_raises_on_malformed_json() -> None:
    client = _client()
    with respx.mock() as mock:
        mock.get(_facts_url(320193)).mock(
            return_value=httpx.Response(
                200, text="<html>nope</html>", headers={"content-type": "text/html"}
            )
        )
        with pytest.raises(SECEdgarError, match="malformed JSON"):
            await client.get_company_facts(320193)


@pytest.mark.unit
async def test_user_agent_header_is_sent() -> None:
    client = _client()
    with respx.mock() as mock:
        route = mock.get(_facts_url(320193)).mock(
            return_value=httpx.Response(200, json={"facts": {}})
        )
        await client.get_company_facts(320193)

    sent = route.calls.last.request
    assert sent.headers.get("User-Agent") == "FinTerminal Tests tests@example.com"
    assert sent.headers.get("Accept") == "application/json"
