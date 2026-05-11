"""Live end-to-end tests against the real Halal Terminal API.

Run with:    pytest -m live -v

Skipped by default (see pytest.ini `addopts = ... -m "not live"`).

Requires HALAL_TERMINAL_API_KEY to be set to a real API key (not the
CI placeholder). If the env var contains the well-known placeholder
prefix `ci-test-` or is empty, all tests are skipped.

These tests validate the SHAPE of the upstream response, not the
specific ratio values (which fluctuate with reporting cycles).
"""

from __future__ import annotations

import os

import pytest

from app.integration.halal_terminal_client import HalalTerminalClient


REFERENCE_TICKERS = ["AAPL", "MSFT", "RIO", "AIXA.DE", "EOG"]


def _is_placeholder_key() -> bool:
    key = os.environ.get("HALAL_TERMINAL_API_KEY", "")
    return (not key) or key.startswith("ci-test-") or key in {"test-ht-key", "test-uuid-key-0000"}


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        _is_placeholder_key(),
        reason="HALAL_TERMINAL_API_KEY is unset or a known placeholder",
    ),
]


@pytest.fixture(scope="module")
def live_client() -> HalalTerminalClient:
    return HalalTerminalClient()  # uses settings (real env var)


@pytest.mark.parametrize("symbol", REFERENCE_TICKERS)
async def test_live_screen_returns_expected_shape(
    live_client: HalalTerminalClient,
    symbol: str,
) -> None:
    payload = await live_client.screen(symbol)

    assert payload is not None, f"{symbol} unexpectedly NOT_COVERED"
    assert isinstance(payload, dict)

    # Required top-level keys per spec §3.2.1
    assert "ratios" in payload
    assert isinstance(payload["ratios"], dict)
    assert "methodologies" in payload
    assert isinstance(payload["methodologies"], dict)

    # At least the 4 bloquant ratios we depend on should be present
    ratios = payload["ratios"]
    for required in (
        "debt_to_marketcap",
        "cash_to_marketcap",
        "impure_revenue_ratio",
    ):
        assert required in ratios, f"{symbol}: missing {required} in upstream payload"
