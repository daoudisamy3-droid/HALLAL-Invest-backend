"""Tests for app/services/financials_service.py.

SecEdgarClient is replaced by an AsyncMock (we test orchestration and
extraction here). The DB is the real Postgres session from conftest
(function-scoped engine, TRUNCATE on teardown).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app.core.exceptions import SECEdgarError
from app.models.financials_cache import FinancialsCache
from app.services.financials_service import _CONCEPT_TAGS, get_financials


# ─── Helpers ────────────────────────────────────────────────────────────────


def _fact(end: str, val: int | float, *, fy: int, accn: str = "0000320193-24-000123",
          form: str = "10-K", filed: str = "2024-11-01", fp: str = "FY") -> dict[str, Any]:
    return {
        "start": "2023-09-25",
        "end": end,
        "val": val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }


def _make_facts(
    entity_name: str = "Apple Inc.",
    *,
    revenues_tag: str = "Revenues",
    revenues_entries: list[dict[str, Any]] | None = None,
    extras: dict[str, dict[str, dict[str, list[dict[str, Any]]]]] | None = None,
) -> dict[str, Any]:
    """Build a SEC-shaped company_facts payload with at least Revenues.

    ``extras`` lets the caller add other us-gaap tags. Structure:
        extras = {"NetIncomeLoss": {"USD": [_fact(...), ...]}}
    """
    us_gaap: dict[str, Any] = {}
    if revenues_entries is None:
        revenues_entries = [_fact("2024-09-28", 391035000000, fy=2024)]
    us_gaap[revenues_tag] = {"label": revenues_tag, "units": {"USD": revenues_entries}}
    if extras:
        for tag, units in extras.items():
            us_gaap[tag] = {"label": tag, "units": units}
    return {
        "cik": 320193,
        "entityName": entity_name,
        "facts": {"us-gaap": us_gaap},
    }


def _mock_client(
    *,
    cik_info: tuple[int, str] | None = (320193, "Apple Inc."),
    facts: dict[str, Any] | None = None,
    raises_lookup: Exception | None = None,
    raises_facts: Exception | None = None,
) -> AsyncMock:
    client = AsyncMock()
    if raises_lookup is not None:
        client.lookup_cik.side_effect = raises_lookup
    else:
        client.lookup_cik.return_value = cik_info
    if raises_facts is not None:
        client.get_company_facts.side_effect = raises_facts
    else:
        client.get_company_facts.return_value = facts
    return client


# ─── AVAILABLE — full happy path ────────────────────────────────────────────


@pytest.mark.integration
async def test_available_snapshot_extracts_anchor_metadata(db) -> None:
    facts = _make_facts(
        entity_name="Apple Inc.",
        extras={
            "NetIncomeLoss": {"USD": [_fact("2024-09-28", 93736000000, fy=2024)]},
            "Assets": {"USD": [_fact("2024-09-28", 364980000000, fy=2024)]},
            "EarningsPerShareDiluted": {
                "USD/shares": [_fact("2024-09-28", 6.08, fy=2024)]
            },
        },
    )
    client = _mock_client(facts=facts)

    report = await get_financials("aapl", db, client)

    assert report.verdict == "AVAILABLE"
    assert report.ticker == "AAPL"
    assert report.cik == 320193
    assert report.entity_name == "Apple Inc."
    assert report.fiscal_year == 2024
    assert str(report.period_end) == "2024-09-28"
    assert report.form == "10-K"
    assert report.accession_number == "0000320193-24-000123"
    assert str(report.filed) == "2024-11-01"
    assert report.revenues == Decimal("391035000000")
    assert report.net_income == Decimal("93736000000")
    assert report.total_assets == Decimal("364980000000")
    assert report.eps_diluted == Decimal("6.08")
    # Concepts not present in facts default to None (NOT auto-zero)
    assert report.capex is None
    assert report.stockholders_equity is None
    assert report.source == "SEC EDGAR API"
    assert report.cached is False


# ─── Multi-tag fallback (Q4) ────────────────────────────────────────────────


@pytest.mark.integration
async def test_revenues_falls_back_to_alternative_tag(db) -> None:
    """When `Revenues` tag is absent, the fallback tag is used as anchor."""
    facts = {
        "entityName": "Microsoft Corporation",
        "facts": {
            "us-gaap": {
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [_fact("2024-06-30", 245122000000, fy=2024)]
                    }
                }
            }
        },
    }
    client = _mock_client(cik_info=(789019, "MICROSOFT CORP"), facts=facts)

    report = await get_financials("MSFT", db, client)
    assert report.verdict == "AVAILABLE"
    assert report.revenues == Decimal("245122000000")
    assert report.fiscal_year == 2024


# ─── Period selection — latest FY + restatement tie-break ───────────────────


@pytest.mark.integration
async def test_latest_fy_picked_with_filed_tiebreaker(db) -> None:
    """Multiple FY entries with the same `end`: pick the one filed last."""
    facts = _make_facts(
        revenues_entries=[
            _fact("2024-09-28", 391000000000, fy=2024,
                  accn="0000320193-24-000001", filed="2024-11-01"),
            # Restated version, same period_end, later filed date:
            _fact("2024-09-28", 391500000000, fy=2024,
                  accn="0000320193-24-000999", filed="2025-02-01"),
            # Older year:
            _fact("2023-09-30", 380000000000, fy=2023,
                  accn="0000320193-23-000001", filed="2023-11-01"),
        ],
    )
    client = _mock_client(facts=facts)
    report = await get_financials("AAPL", db, client)

    assert report.revenues == Decimal("391500000000"), "Latest restatement wins"
    assert report.accession_number == "0000320193-24-000999"


# ─── No FY anchor → ERROR ───────────────────────────────────────────────────


@pytest.mark.integration
async def test_no_fy_revenues_returns_error(db) -> None:
    """Only quarterly data, no FY anchor — verdict ERROR."""
    facts = _make_facts(
        revenues_entries=[
            _fact("2024-06-30", 90000000000, fy=2024, fp="Q3", form="10-Q"),
        ],
    )
    client = _mock_client(facts=facts)
    report = await get_financials("AAPL", db, client)

    assert report.verdict == "ERROR"
    assert "Revenues annuelle" in (report.reason or "")
    # ERROR is not persisted
    rows = (await db.execute(
        select(FinancialsCache).where(FinancialsCache.ticker == "AAPL")
    )).scalars().all()
    assert rows == []


# ─── NOT_COVERED — non-US ticker ────────────────────────────────────────────


@pytest.mark.integration
async def test_unknown_ticker_returns_not_covered(db) -> None:
    client = _mock_client(cik_info=None)  # not in SEC universe
    report = await get_financials("AIXA.DE", db, client)

    assert report.verdict == "NOT_COVERED"
    assert report.cik is None
    assert "non-US" in (report.reason or "") or "univers SEC" in (report.reason or "")
    # No cache row for NOT_COVERED
    rows = (await db.execute(
        select(FinancialsCache).where(FinancialsCache.ticker == "AIXA.DE")
    )).scalars().all()
    assert rows == []


@pytest.mark.integration
async def test_cik_known_but_no_facts_returns_not_covered(db) -> None:
    client = _mock_client(cik_info=(999999, "DEAD CORP"), facts=None)
    report = await get_financials("DEAD", db, client)

    assert report.verdict == "NOT_COVERED"
    assert report.cik == 999999


# ─── ERROR — SECEdgarError raised ───────────────────────────────────────────


@pytest.mark.integration
async def test_sec_edgar_lookup_error_returns_error(db) -> None:
    client = _mock_client(raises_lookup=SECEdgarError("ticker_map returned 503"))
    report = await get_financials("AAPL", db, client)
    assert report.verdict == "ERROR"
    assert "ticker map" in (report.reason or "").lower()
    rows = (await db.execute(
        select(FinancialsCache).where(FinancialsCache.ticker == "AAPL")
    )).scalars().all()
    assert rows == []


@pytest.mark.integration
async def test_sec_edgar_facts_error_returns_error(db) -> None:
    client = _mock_client(
        cik_info=(320193, "Apple Inc."),
        raises_facts=SECEdgarError("upstream 500"),
    )
    report = await get_financials("AAPL", db, client)
    assert report.verdict == "ERROR"
    assert report.cik == 320193  # CIK from lookup still surfaced
    assert "company_facts" in (report.reason or "")


# ─── Cache miss/hit/stale ───────────────────────────────────────────────────


@pytest.mark.integration
async def test_cache_miss_persists_facts(db) -> None:
    facts = _make_facts()
    client = _mock_client(facts=facts)
    await get_financials("AAPL", db, client)

    rows = (await db.execute(
        select(FinancialsCache).where(FinancialsCache.ticker == "AAPL")
    )).scalars().all()
    assert len(rows) == 1
    assert rows[0].cik == 320193
    assert "Revenues" in rows[0].facts_json["facts"]["us-gaap"]


@pytest.mark.integration
async def test_cache_hit_within_ttl_skips_upstream(db) -> None:
    facts = _make_facts()
    client = _mock_client(facts=facts)

    first = await get_financials("AAPL", db, client)
    assert first.cached is False
    assert client.get_company_facts.await_count == 1

    second = await get_financials("AAPL", db, client)
    assert second.cached is True
    assert second.source == "cache (financials_cache)"
    assert second.cache_age_hours is not None and second.cache_age_hours < 0.1
    assert second.verdict == "AVAILABLE"
    assert second.revenues == first.revenues
    # Client NOT called again (cache short-circuits even the CIK lookup)
    assert client.get_company_facts.await_count == 1


@pytest.mark.integration
async def test_stale_row_outside_ttl_triggers_refetch(db) -> None:
    """A row older than FINANCIALS_CACHE_TTL_HOURS (24h) must be refetched."""
    stale = FinancialsCache(
        id=uuid.uuid4(),
        ticker="AAPL",
        cik=320193,
        fetched_at=datetime.now(tz=timezone.utc) - timedelta(hours=25),
        facts_json=_make_facts(),
    )
    db.add(stale)
    await db.commit()

    fresh_facts = _make_facts(entity_name="Apple Inc. (refetched)")
    client = _mock_client(facts=fresh_facts)
    report = await get_financials("AAPL", db, client)

    assert report.cached is False
    assert report.entity_name == "Apple Inc. (refetched)"
    assert client.get_company_facts.await_count == 1


# ─── Missing concept ⇒ field is None, NOT auto-zero ─────────────────────────


@pytest.mark.integration
async def test_missing_concept_yields_none_not_zero(db) -> None:
    """Lesson from étape 1.5: never default a missing financial to 0."""
    facts = _make_facts(
        # Only Revenues — no NetIncomeLoss, Assets, …
        revenues_entries=[_fact("2024-09-28", 391000000000, fy=2024)],
    )
    client = _mock_client(facts=facts)
    report = await get_financials("AAPL", db, client)

    assert report.verdict == "AVAILABLE"
    assert report.revenues == Decimal("391000000000")
    assert report.net_income is None
    assert report.total_assets is None
    assert report.eps_diluted is None


# ─── Step 7.3.D — tag-aggregation regression tests ──────────────────────────


def _xbrl(tag_to_entries: dict[str, list[dict[str, Any]]], unit: str = "USD") -> dict[str, Any]:
    """Build a minimal companyfacts payload with multiple tags."""
    return {
        "entityName": "TestCo",
        "facts": {
            "us-gaap": {
                tag: {"units": {unit: entries}}
                for tag, entries in tag_to_entries.items()
            }
        },
    }


def _fy(end: str, val: int | float, *, filed: str | None = None) -> dict[str, Any]:
    return {
        "fp": "FY",
        "end": end,
        "filed": filed or end,
        "val": val,
        "fy": int(end[:4]),
        "form": "10-K",
        "accn": f"0000000000-{end[:4]}-000001",
    }


@pytest.mark.unit
def test_extract_latest_annual_aggregates_across_tags() -> None:
    """The legacy ``Revenues`` tag stops in 2018; the modern tag continues to 2024.

    A first-tag-wins fallback would return the FY 2018 entry. The fixed
    extractor aggregates FY entries from BOTH tags and picks 2024.
    """
    from app.services.financials_service import _extract_latest_annual

    payload = _xbrl({
        "Revenues": [
            _fy("2016-12-31", 215639000000),
            _fy("2017-12-31", 229234000000),
            _fy("2018-12-31", 265595000000),  # last year of legacy tag
        ],
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            _fy("2019-09-28", 260174000000),
            _fy("2020-09-26", 274515000000),
            _fy("2021-09-25", 365817000000),
            _fy("2022-09-24", 394328000000),
            _fy("2023-09-30", 383285000000),
            _fy("2024-09-28", 391035000000),  # most recent
        ],
    })

    entry = _extract_latest_annual(
        payload,
        ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
        "USD",
    )
    assert entry is not None
    assert entry["end"] == "2024-09-28"
    assert entry["fy"] == 2024
    assert entry["val"] == 391035000000


@pytest.mark.unit
def test_extract_latest_annual_handles_modern_tag_only() -> None:
    """Sanity: when only the modern tag has data, we still find it."""
    from app.services.financials_service import _extract_latest_annual
    payload = _xbrl({
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            _fy("2024-09-28", 100),
        ],
    })
    entry = _extract_latest_annual(
        payload,
        ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"],
        "USD",
    )
    assert entry is not None and entry["val"] == 100


@pytest.mark.unit
def test_extract_n_year_annuals_aggregates_across_tags() -> None:
    """Same bug, same fix: ``extract_n_year_annuals`` must merge FY entries
    from every tag — otherwise step-4 scores (Piotroski / Growth / etc.)
    silently drop years that migrated to a modern tag."""
    from app.services.financials_service import extract_n_year_annuals

    payload = _xbrl({
        "Revenues": [
            _fy("2018-12-31", 265),
        ],
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            _fy("2022-09-24", 394),
            _fy("2023-09-30", 383),
            _fy("2024-09-28", 391),
        ],
    })

    series = extract_n_year_annuals(payload, "revenues", n=5)
    # Most-recent-first, mixing both tags: 2024, 2023, 2022, 2018
    ends = [d.isoformat() for d, _v in series]
    assert ends == ["2024-09-28", "2023-09-30", "2022-09-24", "2018-12-31"]
    vals = [v for _d, v in series]
    assert vals[0] == Decimal("391")  # latest is from the modern tag
    assert vals[-1] == Decimal("265")  # oldest is from the legacy tag


@pytest.mark.unit
def test_extract_n_year_annuals_dedupes_overlapping_end_dates() -> None:
    """When legacy + modern tags both report the same end date, keep the
    entry with the later ``filed`` (treat as restatement)."""
    from app.services.financials_service import extract_n_year_annuals

    payload = _xbrl({
        "Revenues": [
            _fy("2018-12-31", 100, filed="2019-01-01"),
        ],
        "RevenueFromContractWithCustomerExcludingAssessedTax": [
            _fy("2018-12-31", 110, filed="2020-06-15"),  # restatement, wins
        ],
    })

    series = extract_n_year_annuals(payload, "revenues", n=5)
    assert len(series) == 1
    assert series[0][1] == Decimal("110")


# ─── Step 8 Phase A — universal coverage regression tests ───────────────────


def _xbrl_multi(
    taxonomies: dict[str, dict[str, list[dict[str, Any]]]],
    unit: str = "USD",
) -> dict[str, Any]:
    """Build a multi-taxonomy companyfacts payload.

    ``taxonomies`` shape:  ``{taxonomy: {tag: [entry, ...]}, ...}``
    """
    return {
        "entityName": "TestCo",
        "facts": {
            tax: {
                tag: {"units": {unit: entries}}
                for tag, entries in tags.items()
            }
            for tax, tags in taxonomies.items()
        },
    }


def _foreign_annual(
    end: str,
    val: int | float,
    *,
    form: str = "20-F",
    start: str | None = None,
    filed: str | None = None,
) -> dict[str, Any]:
    """Build an annual entry shaped like a foreign filer would emit it:
    NO ``fp`` field, but a ``form`` in {20-F, 40-F} and ~12-month period.
    """
    if start is None:
        # default: 365 days before end
        from datetime import date as _d, timedelta as _t
        start = (_d.fromisoformat(end) - _t(days=365)).isoformat()
    return {
        "end": end,
        "start": start,
        "filed": filed or end,
        "val": val,
        "fy": int(end[:4]),
        "form": form,
        "accn": f"0000000000-{end[:4]}-000001",
    }


@pytest.mark.unit
def test_extract_latest_annual_accepts_20F_foreign_filing() -> None:
    """A 20-F foreign filer reports under ifrs-full taxonomy with no fp=FY
    marker. The extractor must still recognise the entry as annual based
    on form + period duration."""
    from app.services.financials_service import _extract_latest_annual
    payload = _xbrl_multi({
        "ifrs-full": {
            "Revenue": [
                _foreign_annual("2024-12-31", 1_500_000_000, form="20-F",
                                start="2024-01-01"),
            ],
        },
    })
    entry = _extract_latest_annual(payload, _CONCEPT_TAGS["revenues"], "USD")
    assert entry is not None
    assert entry["val"] == 1_500_000_000
    assert entry["form"] == "20-F"


@pytest.mark.unit
def test_extract_latest_annual_accepts_40F_canadian_filing() -> None:
    from app.services.financials_service import _extract_latest_annual
    payload = _xbrl_multi({
        "ifrs-full": {
            "ProfitLoss": [
                _foreign_annual("2024-12-31", 200_000_000, form="40-F",
                                start="2024-01-01"),
            ],
        },
    })
    entry = _extract_latest_annual(payload, _CONCEPT_TAGS["net_income"], "USD")
    assert entry is not None
    assert entry["val"] == 200_000_000


@pytest.mark.unit
def test_extract_latest_annual_rejects_quarterly_form() -> None:
    """A 10-Q filing with the same shape must NOT be picked up as annual."""
    from app.services.financials_service import _extract_latest_annual
    quarterly = {
        "end": "2024-06-30",
        "start": "2024-04-01",   # ~90 days, not annual
        "filed": "2024-07-15",
        "val": 100,
        "fy": 2024,
        "fp": "Q2",
        "form": "10-Q",
        "accn": "0000000000-24-000002",
    }
    payload = _xbrl_multi({"us-gaap": {"Revenues": [quarterly]}})
    entry = _extract_latest_annual(payload, ["Revenues"], "USD")
    assert entry is None


@pytest.mark.unit
def test_extract_latest_annual_prefers_most_recent_across_taxonomies() -> None:
    """Mixed taxonomies (rare but possible for dual-listed companies):
    pick the most recent end date regardless of which taxonomy hosts it."""
    from app.services.financials_service import _extract_latest_annual
    payload = _xbrl_multi({
        "us-gaap": {
            "Revenues": [_fy("2022-12-31", 100)],
        },
        "ifrs-full": {
            "Revenue": [_foreign_annual("2024-12-31", 200, form="20-F",
                                        start="2024-01-01")],
        },
    })
    entry = _extract_latest_annual(payload, _CONCEPT_TAGS["revenues"], "USD")
    assert entry is not None
    assert entry["val"] == 200
    assert entry["end"] == "2024-12-31"


@pytest.mark.unit
def test_extract_latest_annual_uses_expanded_gaap_tag_for_banks() -> None:
    """Bank-like filers use InterestAndDividendIncomeOperating instead of
    Revenues. Step 8 added it to the revenue tag list."""
    from app.services.financials_service import _extract_latest_annual
    # Build a "bank" payload: only the interest-income-operating tag.
    payload = _xbrl_multi({
        "us-gaap": {
            "InterestAndDividendIncomeOperating": [_fy("2024-12-31", 50_000_000)],
        },
    })
    # NOT in revenues list — but it IS in interest_income_operating list.
    entry = _extract_latest_annual(
        payload, _CONCEPT_TAGS["interest_income_operating"], "USD"
    )
    assert entry is not None
    assert entry["val"] == 50_000_000


@pytest.mark.unit
def test_extract_n_year_annuals_walks_both_taxonomies() -> None:
    from app.services.financials_service import extract_n_year_annuals
    payload = _xbrl_multi({
        "us-gaap": {
            "Revenues": [_fy("2022-12-31", 100)],
        },
        "ifrs-full": {
            "Revenue": [
                _foreign_annual("2023-12-31", 200, form="20-F", start="2023-01-01"),
                _foreign_annual("2024-12-31", 250, form="20-F", start="2024-01-01"),
            ],
        },
    })
    series = extract_n_year_annuals(payload, "revenues", n=5)
    ends = [d.isoformat() for d, _ in series]
    assert ends == ["2024-12-31", "2023-12-31", "2022-12-31"]


@pytest.mark.unit
def test_is_annual_entry_helper_boundary_cases() -> None:
    """320..400 day windows are accepted; outside the window is rejected."""
    from app.services.financials_service import _is_annual_entry
    # Exactly 365 days, 20-F → accepted
    assert _is_annual_entry({
        "end": "2024-12-31", "start": "2024-01-01", "form": "20-F",
    })
    # 365 days, 10-K (no fp) → accepted via form whitelist
    assert _is_annual_entry({
        "end": "2024-12-31", "start": "2024-01-01", "form": "10-K",
    })
    # 90 days, 10-Q → rejected
    assert not _is_annual_entry({
        "end": "2024-06-30", "start": "2024-04-01", "form": "10-Q",
    })
    # No end → rejected
    assert not _is_annual_entry({"start": "2024-01-01", "form": "10-K"})
    # fp=FY without start (US 10-K shape often omits start for instantaneous
    # tags like Assets) → accepted on fp alone
    assert _is_annual_entry({"end": "2024-12-31", "fp": "FY"})


# ─── Step 8 Phase A — EXEMPT_SIC must NOT short-circuit the pipeline ────────


@pytest.mark.unit
def test_exempt_sic_only_affects_fraud_signal_1_not_pipeline() -> None:
    """A capital-intensive ticker (SIC 1311 = Oil & Gas) is exempt from the
    absolute FCF/NI threshold (Fraud Signal 1) but must still receive
    Piotroski / Growth / Capital scores as usual."""
    from app.services.sector import classify_from_submissions

    submissions = {"sicCode": "1311", "sicDescription": "Crude Petroleum and Natural Gas"}
    sector = classify_from_submissions(submissions)
    assert sector.is_exempt_from_absolute_fcf_ni is True

    # Now confirm: nothing in the codebase calls a "early return" based on
    # is_exempt_from_absolute_fcf_ni outside of fraud._signal_1_fcf_ni.
    # Use grep at test time as a guard.
    import pathlib
    src_root = pathlib.Path(__file__).parent.parent / "app"
    callers: list[str] = []
    for path in src_root.rglob("*.py"):
        if path.name == "fraud.py" or path.name == "sector.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "is_exempt_from_absolute_fcf_ni" in text:
            callers.append(str(path.relative_to(src_root)))
    assert callers == [], (
        f"EXEMPT_SIC short-circuit detected outside fraud/sector: {callers}"
    )
