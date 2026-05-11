"""Unit tests for app/integration/halal_terminal_client.py.

Uses respx to intercept httpx calls at the transport layer. No live
API access — see tests/test_halal_terminal_live.py for that.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from app.core.exceptions import HalalTerminalError
from app.integration.halal_terminal_client import HalalTerminalClient


BASE_URL = "https://api.halalterminal.com"


def _client() -> HalalTerminalClient:
    return HalalTerminalClient(
        base_url=BASE_URL,
        api_key="test-ht-key",
        timeout_s=2.0,
    )


@pytest.mark.unit
async def test_screen_returns_dict_on_200() -> None:
    client = _client()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/api/screen/AAPL").mock(
            return_value=httpx.Response(
                200,
                json={"symbol": "AAPL", "ratios": {"debt_to_marketcap": 0.02}},
            )
        )
        result = await client.screen("AAPL")

    assert result is not None
    assert result["symbol"] == "AAPL"
    assert route.call_count == 1


@pytest.mark.unit
async def test_screen_returns_none_on_404() -> None:
    client = _client()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/api/screen/UNKNOWN").mock(
            return_value=httpx.Response(404, json={"error": "not covered"})
        )
        result = await client.screen("UNKNOWN")

    assert result is None
    assert route.call_count == 1  # no retries on 404


@pytest.mark.unit
async def test_screen_raises_on_401_without_retry() -> None:
    client = _client()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/api/screen/AAPL").mock(
            return_value=httpx.Response(401, json={"error": "bad key"})
        )
        with pytest.raises(HalalTerminalError, match="401"):
            await client.screen("AAPL")

    assert route.call_count == 1  # no retry on 4xx (other than 404)


@pytest.mark.unit
async def test_screen_retries_then_fails_on_500() -> None:
    client = _client()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/api/screen/AAPL").mock(
            return_value=httpx.Response(500, text="boom")
        )
        with pytest.raises(HalalTerminalError):
            await client.screen("AAPL")

    assert route.call_count == 3  # 1 initial + 2 retries


@pytest.mark.unit
async def test_screen_retries_on_timeout() -> None:
    client = _client()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/api/screen/AAPL").mock(
            side_effect=httpx.TimeoutException("slow upstream")
        )
        with pytest.raises(HalalTerminalError):
            await client.screen("AAPL")

    assert route.call_count == 3


@pytest.mark.unit
async def test_screen_recovers_after_transient_500() -> None:
    """First call 500, second succeeds — tenacity should retry once and return."""
    client = _client()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/api/screen/AAPL").mock(
            side_effect=[
                httpx.Response(500, text="transient"),
                httpx.Response(200, json={"symbol": "AAPL", "ratios": {}}),
            ]
        )
        result = await client.screen("AAPL")

    assert result == {"symbol": "AAPL", "ratios": {}}
    assert route.call_count == 2


@pytest.mark.unit
async def test_screen_raises_on_malformed_json() -> None:
    client = _client()
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/screen/AAPL").mock(
            return_value=httpx.Response(
                200,
                text="<html>not json</html>",
                headers={"content-type": "text/html"},
            )
        )
        with pytest.raises(HalalTerminalError, match="malformed JSON"):
            await client.screen("AAPL")


@pytest.mark.unit
async def test_screen_raises_on_non_object_json() -> None:
    client = _client()
    with respx.mock(base_url=BASE_URL) as mock:
        mock.post("/api/screen/AAPL").mock(
            return_value=httpx.Response(200, json=["not", "a", "dict"])
        )
        with pytest.raises(HalalTerminalError, match="non-object"):
            await client.screen("AAPL")


@pytest.mark.unit
async def test_screen_sends_api_key_header() -> None:
    client = _client()
    with respx.mock(base_url=BASE_URL) as mock:
        route = mock.post("/api/screen/AAPL").mock(
            return_value=httpx.Response(200, json={"symbol": "AAPL"})
        )
        await client.screen("AAPL")

    assert route.call_count == 1
    sent_request = route.calls.last.request
    assert sent_request.headers.get("X-API-Key") == "test-ht-key"
    assert sent_request.headers.get("Accept") == "application/json"
    assert str(sent_request.url) == f"{BASE_URL}/api/screen/AAPL"
