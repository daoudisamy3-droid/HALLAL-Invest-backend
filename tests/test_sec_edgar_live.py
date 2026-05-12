"""Live end-to-end tests against the real SEC EDGAR API.

Run with:    pytest -m live -v

Skipped by default (pytest.ini ``addopts = ... -m "not live"``).

Requires ``SEC_EDGAR_USER_AGENT`` to identify a real application + contact
email (SEC's policy). The CI placeholder is detected and the suite is
skipped automatically.

These tests validate that the client + service can extract a meaningful
snapshot for the 5 reference tickers (AAPL / MSFT / NVDA / AMZN / EOG —
all US-listed, all expected to be in SEC EDGAR's universe).
"""

from __future__ import annotations

import os

import pytest

from app.integration.sec_edgar_client import SecEdgarClient


REFERENCE_TICKERS = ["AAPL", "MSFT", "NVDA", "AMZN", "EOG"]


def _is_placeholder_user_agent() -> bool:
    ua = os.environ.get("SEC_EDGAR_USER_AGENT", "")
    placeholder_markers = ("ci-test", "example.com", "FinTerminal CI")
    return (not ua) or any(m in ua for m in placeholder_markers)


pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        _is_placeholder_user_agent(),
        reason="SEC_EDGAR_USER_AGENT is unset or a known placeholder",
    ),
]


@pytest.fixture(scope="module")
def live_client() -> SecEdgarClient:
    return SecEdgarClient()  # uses settings (real env vars)


@pytest.mark.parametrize("ticker", REFERENCE_TICKERS)
async def test_live_lookup_cik_succeeds(
    live_client: SecEdgarClient, ticker: str
) -> None:
    """SEC must know about all 5 reference US tickers."""
    cik_info = await live_client.lookup_cik(ticker)
    assert cik_info is not None, f"{ticker} unexpectedly missing from SEC ticker map"
    cik, title = cik_info
    assert isinstance(cik, int) and cik > 0
    assert isinstance(title, str) and len(title) > 0


@pytest.mark.parametrize("ticker", REFERENCE_TICKERS)
async def test_live_company_facts_have_revenues(
    live_client: SecEdgarClient, ticker: str
) -> None:
    """Every reference ticker should expose at least one Revenues FY entry."""
    cik_info = await live_client.lookup_cik(ticker)
    assert cik_info is not None
    cik, _name = cik_info

    facts = await live_client.get_company_facts(cik)
    assert facts is not None
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    assert isinstance(us_gaap, dict)

    # At least one of the Revenues-equivalent tags must exist.
    revenue_tags = (
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    )
    found_tag = next((t for t in revenue_tags if t in us_gaap), None)
    assert found_tag is not None, f"{ticker}: no Revenues-equivalent tag in facts"

    # Check at least one FY entry exists
    units = us_gaap[found_tag].get("units", {}).get("USD", [])
    annual = [e for e in units if isinstance(e, dict) and e.get("fp") == "FY"]
    assert annual, f"{ticker}: no FY entries under {found_tag}/USD"
