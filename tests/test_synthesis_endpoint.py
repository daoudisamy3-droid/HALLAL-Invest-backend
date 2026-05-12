"""HTTP tests for GET /api/v1/synthesis/{symbol} (Step 6)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport

from app.api.v1.endpoints.synthesis import _db_session
from app.core.config import settings
from app.integration.halal_terminal_client import get_halal_terminal_client
from app.integration.sec_edgar_client import get_sec_edgar_client
from app.integration.yfinance_client import get_yfinance_client
from app.main import app
from app.schemas.investissable import InvestissableReport
from app.schemas.shariah import ShariahReport
from app.schemas.valuation import ValuationReport
from app.services import synthesis_service as ss


TEST_KEY = "test-uuid-key-0000"


@pytest_asyncio.fixture()
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "FINTERMINAL_API_KEY", TEST_KEY)

    async def _override_db():
        yield db

    app.dependency_overrides[_db_session] = _override_db
    app.dependency_overrides[get_halal_terminal_client] = lambda: AsyncMock()
    app.dependency_overrides[get_sec_edgar_client] = lambda: AsyncMock()
    app.dependency_overrides[get_yfinance_client] = lambda: AsyncMock()

    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()


def _patch(monkeypatch, halal_verdict="PASS", inv_verdict="OUI", val_verdict="OUI_NEUTRE"):
    async def halal_co(*_a, **_kw):
        return ShariahReport(
            symbol="AAPL",
            verdict=halal_verdict,  # type: ignore[arg-type]
            source="Halal Terminal API (aggregate verdict)",
        )

    async def inv_co(*_a, **_kw):
        return InvestissableReport(
            symbol="AAPL",
            verdict=inv_verdict,  # type: ignore[arg-type]
            label="QUALITÉ EXCELLENTE" if inv_verdict == "OUI" else None,
            quality_score=72.0 if inv_verdict == "OUI" else None,
            data_completeness="FULL" if inv_verdict == "OUI" else "INSUFFICIENT",
        )

    async def val_co(*_a, **_kw):
        return ValuationReport(
            symbol="AAPL",
            verdict=val_verdict,  # type: ignore[arg-type]
            label="JUSTE PRIX" if val_verdict == "OUI_NEUTRE" else None,
            confidence="N/A",
            n_methods=0,
        )

    monkeypatch.setattr(ss.shariah_service, "screen_with_personal_thresholds", halal_co)
    monkeypatch.setattr(ss.investissable_service, "compute_investissable", inv_co)
    monkeypatch.setattr(ss.valuation_service, "compute_valuation", val_co)


# ─── Auth ───────────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_unauthenticated_returns_401(client: httpx.AsyncClient) -> None:
    resp = await client.get("/api/v1/synthesis/AAPL")
    assert resp.status_code == 401


# ─── Happy path ─────────────────────────────────────────────────────────────


@pytest.mark.integration
async def test_synthesis_all_green_returns_investable(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    _patch(monkeypatch, halal_verdict="PASS", inv_verdict="OUI", val_verdict="OUI_NEUTRE")
    resp = await client.get(
        "/api/v1/synthesis/AAPL",
        headers={"X-Auth-Token": TEST_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "AAPL"
    assert body["overall_verdict"] == "INVESTABLE"
    assert body["halal"]["verdict"] == "PASS"
    assert body["investissable"]["verdict"] == "OUI"
    assert body["valuation"]["verdict"] == "OUI_NEUTRE"
    assert body["errors"] == []


@pytest.mark.integration
async def test_synthesis_normalises_symbol_to_uppercase(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    _patch(monkeypatch)
    resp = await client.get(
        "/api/v1/synthesis/aapl",
        headers={"X-Auth-Token": TEST_KEY},
    )
    assert resp.status_code == 200
    assert resp.json()["symbol"] == "AAPL"


@pytest.mark.integration
async def test_synthesis_halal_fail_returns_blocked_in_200_body(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    """BLOCKED is a body-level verdict, not a 4xx status."""
    _patch(monkeypatch, halal_verdict="FAIL")
    resp = await client.get(
        "/api/v1/synthesis/AAPL",
        headers={"X-Auth-Token": TEST_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_verdict"] == "BLOCKED"
    assert "NON HALAL" in body["overall_label"]


@pytest.mark.integration
async def test_synthesis_layer_crash_returns_200_with_errors_in_body(
    client: httpx.AsyncClient, monkeypatch
) -> None:
    """When valuation_service raises, the endpoint MUST stay 200, not 500."""

    async def halal_co(*_a, **_kw):
        return ShariahReport(
            symbol="AAPL",
            verdict="PASS",
            source="Halal Terminal API (aggregate verdict)",
        )

    async def inv_co(*_a, **_kw):
        return InvestissableReport(
            symbol="AAPL",
            verdict="OUI",
            label="QUALITÉ EXCELLENTE",
            quality_score=72.0,
            data_completeness="FULL",
        )

    async def val_co(*_a, **_kw):
        raise RuntimeError("yfinance down")

    monkeypatch.setattr(ss.shariah_service, "screen_with_personal_thresholds", halal_co)
    monkeypatch.setattr(ss.investissable_service, "compute_investissable", inv_co)
    monkeypatch.setattr(ss.valuation_service, "compute_valuation", val_co)

    resp = await client.get(
        "/api/v1/synthesis/AAPL",
        headers={"X-Auth-Token": TEST_KEY},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["overall_verdict"] == "REQUIRES_REVIEW"
    assert body["valuation"]["available"] is False
    assert "yfinance" in body["valuation"]["error"]
    assert len(body["errors"]) == 1
