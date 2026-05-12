"""Financials service — SEC EDGAR orchestration + extraction (Step 2).

Spec authority:
  - §3.2.2 — SEC EDGAR endpoints, the 11 critical XBRL concepts, cache TTL 24h.
  - master-prompt §1 — mapping: §3.2.2 → ``app/integration/sec_edgar_client.py``.
    This service is the V1 thin orchestration layer on top.
  - §9.2 étape 2 — livrables.

Cache layer (Q1 plan validated): a Postgres ``financials_cache`` table
holding the full ``company_facts`` JSONB blob. TTL =
``settings.FINANCIALS_CACHE_TTL_HOURS`` (24h). The deserialiser
rebuilds the snapshot from the cached blob on every cache hit — cheap.

Verdict semantics (Q3 plan harmonised with ShariahReport):
  - AVAILABLE   : metadata + 11 concept values populated
  - NOT_COVERED : ticker not in SEC universe — NOT cached (coverage grows)
  - ERROR       : transient upstream failure — NOT cached

Step-2 deliberately exposes only raw extracted values. No derivative
arithmetic (no ``total_debt = LongTermDebt + ShortTermBorrowings``, no
TTM, no ratios). Step 4 will own those.
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import SECEdgarError
from app.integration.sec_edgar_client import SecEdgarClient
from app.models.financials_cache import FinancialsCache
from app.schemas.financials import FinancialsSnapshot

logger = logging.getLogger(__name__)


# ─── Concept-tag fallback map (Q4 plan validated) ────────────────────────────


# Each financial concept maps to an ordered list of US-GAAP tags to try.
# The first tag that yields at least one FY (annual) entry wins.
_CONCEPT_TAGS: dict[str, list[str]] = {
    "revenues": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "net_income": ["NetIncomeLoss"],
    "total_assets": ["Assets"],
    "long_term_debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "short_term_borrowings": ["ShortTermBorrowings", "DebtCurrent"],
    "cash_and_equivalents": ["CashAndCashEquivalentsAtCarryingValue", "Cash"],
    "interest_income_operating": ["InterestIncomeOperating"],
    "eps_diluted": ["EarningsPerShareDiluted"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "stockholders_equity": ["StockholdersEquity"],
}

# Most concepts are USD; EPS is reported as USD/shares.
_CONCEPT_UNITS: dict[str, str] = {
    "eps_diluted": "USD/shares",
}


# ─── Source strings (aligned with schema Literal) ───────────────────────────


_SOURCE_FRESH = "SEC EDGAR API"
_SOURCE_CACHE = "cache (financials_cache)"


# ─── Public API ──────────────────────────────────────────────────────────────


async def get_financials(
    ticker: str,
    db: AsyncSession,
    client: SecEdgarClient,
) -> FinancialsSnapshot:
    """Resolve the latest annual financials snapshot for ``ticker``.

    Uses the ``financials_cache`` table as a 24h cache; falls back to a
    live SEC EDGAR call on cache miss/stale.
    """
    tk = ticker.strip().upper()

    cached = await _read_cache(db, tk)
    if cached is not None:
        return cached

    # CIK lookup — may raise SECEdgarError if the ticker map itself is down.
    try:
        cik_info = await client.lookup_cik(tk)
    except SECEdgarError as exc:
        logger.warning("financials_service ERROR ticker=%s cik_lookup err=%s", tk, exc)
        return FinancialsSnapshot(
            ticker=tk,
            verdict="ERROR",
            source=_SOURCE_FRESH,
            reason=f"SEC EDGAR ticker map indisponible: {exc}",
        )

    if cik_info is None:
        # Ticker not in SEC's universe — most likely non-US listing.
        return FinancialsSnapshot(
            ticker=tk,
            verdict="NOT_COVERED",
            source=_SOURCE_FRESH,
            reason=(
                f"{tk} non couvert par SEC EDGAR (probablement un ticker non-US "
                "ou inconnu de l'univers SEC)."
            ),
        )

    cik, entity_name = cik_info

    try:
        facts = await client.get_company_facts(cik)
    except SECEdgarError as exc:
        logger.warning("financials_service ERROR ticker=%s cik=%s err=%s", tk, cik, exc)
        return FinancialsSnapshot(
            ticker=tk,
            cik=cik,
            entity_name=entity_name,
            verdict="ERROR",
            source=_SOURCE_FRESH,
            reason=f"SEC EDGAR company_facts indisponible: {exc}",
        )

    if facts is None:
        # CIK known but no facts (rare; usually a de-listed shell).
        return FinancialsSnapshot(
            ticker=tk,
            cik=cik,
            entity_name=entity_name,
            verdict="NOT_COVERED",
            source=_SOURCE_FRESH,
            reason=f"SEC EDGAR n'a pas de company_facts pour CIK {cik:010d}.",
        )

    snapshot = _build_snapshot_from_facts(tk, cik, entity_name, facts)

    # Cache the raw facts blob if we managed to extract something useful.
    if snapshot.verdict == "AVAILABLE":
        await _persist(db, tk, cik, facts)

    return snapshot


# ─── Extraction ─────────────────────────────────────────────────────────────


def _build_snapshot_from_facts(
    ticker: str,
    cik: int,
    entity_name_from_cik_map: str,
    facts: dict[str, Any],
) -> FinancialsSnapshot:
    """Extract the 11 critical concepts from a SEC company_facts payload.

    Anchor = latest FY entry of ``Revenues`` (or its equivalent tags).
    Without it we can't fill the filing metadata (fiscal_year, period_end,
    form, accession_number, filed) — surface that as a verdict ERROR.
    """
    # Prefer the entityName from the SEC payload (canonical); fall back to
    # the title from the ticker map.
    entity_name = str(facts.get("entityName", "")) or entity_name_from_cik_map

    anchor = _extract_latest_annual(facts, _CONCEPT_TAGS["revenues"], "USD")
    if anchor is None:
        logger.warning(
            "financials_service no FY revenues anchor ticker=%s cik=%s", ticker, cik
        )
        return FinancialsSnapshot(
            ticker=ticker,
            cik=cik,
            entity_name=entity_name,
            verdict="ERROR",
            source=_SOURCE_FRESH,
            reason=(
                "Aucune donnée Revenues annuelle (fp=FY) trouvée dans SEC EDGAR — "
                "métadonnées de filing indisponibles."
            ),
        )

    def get_decimal(concept_name: str) -> Decimal | None:
        unit = _CONCEPT_UNITS.get(concept_name, "USD")
        entry = _extract_latest_annual(facts, _CONCEPT_TAGS[concept_name], unit)
        if entry is None:
            return None
        return _to_decimal(entry.get("val"))

    return FinancialsSnapshot(
        ticker=ticker,
        cik=cik,
        entity_name=entity_name,
        verdict="AVAILABLE",
        fiscal_year=int(anchor["fy"]),
        period_end=_to_date(anchor.get("end")),
        form=str(anchor.get("form", "") or "") or None,
        accession_number=str(anchor.get("accn", "") or "") or None,
        filed=_to_date(anchor.get("filed")),
        revenues=_to_decimal(anchor.get("val")),
        net_income=get_decimal("net_income"),
        total_assets=get_decimal("total_assets"),
        long_term_debt=get_decimal("long_term_debt"),
        short_term_borrowings=get_decimal("short_term_borrowings"),
        cash_and_equivalents=get_decimal("cash_and_equivalents"),
        interest_income_operating=get_decimal("interest_income_operating"),
        eps_diluted=get_decimal("eps_diluted"),
        operating_cash_flow=get_decimal("operating_cash_flow"),
        capex=get_decimal("capex"),
        stockholders_equity=get_decimal("stockholders_equity"),
        source=_SOURCE_FRESH,
    )


def _extract_latest_annual(
    facts: dict[str, Any],
    tags: list[str],
    unit: str,
) -> dict[str, Any] | None:
    """Return the latest FY entry for any tag in ``tags`` under ``unit``.

    Walks the SEC payload at ``facts['facts']['us-gaap'][tag]['units'][unit]``.
    Filters to ``fp == 'FY'``. Sorts by ``end`` desc, then ``filed`` desc to
    pick the most recently restated value when multiple accession numbers
    share the same period end.
    """
    us_gaap = facts.get("facts", {}).get("us-gaap", {})
    if not isinstance(us_gaap, dict):
        return None
    for tag in tags:
        tag_data = us_gaap.get(tag)
        if not isinstance(tag_data, dict):
            continue
        units = tag_data.get("units", {})
        if not isinstance(units, dict):
            continue
        entries = units.get(unit)
        if not isinstance(entries, list):
            continue
        annual = [
            e for e in entries
            if isinstance(e, dict) and e.get("fp") == "FY" and e.get("end")
        ]
        if not annual:
            continue
        annual.sort(
            key=lambda e: (str(e.get("end", "")), str(e.get("filed", ""))),
            reverse=True,
        )
        return annual[0]
    return None


def _to_decimal(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def _to_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


# ─── Cache layer (financials_cache table) ────────────────────────────────────


async def _read_cache(db: AsyncSession, ticker: str) -> FinancialsSnapshot | None:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(
        hours=settings.FINANCIALS_CACHE_TTL_HOURS
    )
    stmt = (
        select(FinancialsCache)
        .where(FinancialsCache.ticker == ticker)
        .where(FinancialsCache.fetched_at >= cutoff)
        .order_by(FinancialsCache.fetched_at.desc())
        .limit(1)
    )
    result = await db.execute(stmt)
    row = result.scalar_one_or_none()
    if row is None:
        return None

    try:
        snapshot = _build_snapshot_from_facts(
            ticker,
            row.cik,
            entity_name_from_cik_map="",
            facts=row.facts_json or {},
        )
    except Exception as exc:
        logger.warning(
            "financials_service cache row deserialize failed ticker=%s err=%s — refetching",
            ticker, exc,
        )
        return None

    # Override the source / cached / cache_age_hours on the freshly-built object.
    fetched_at = row.fetched_at
    if fetched_at.tzinfo is None:
        fetched_at = fetched_at.replace(tzinfo=timezone.utc)
    age_hours = (datetime.now(tz=timezone.utc) - fetched_at).total_seconds() / 3600

    snapshot.source = _SOURCE_CACHE
    snapshot.cached = True
    snapshot.cache_age_hours = round(age_hours, 2)
    return snapshot


async def _persist(
    db: AsyncSession,
    ticker: str,
    cik: int,
    facts: dict[str, Any],
) -> None:
    """Insert a financials_cache row. Errors logged, not propagated."""
    try:
        row = FinancialsCache(
            id=uuid.uuid4(),
            ticker=ticker,
            cik=cik,
            fetched_at=datetime.now(tz=timezone.utc),
            facts_json=facts,
        )
        db.add(row)
        await db.commit()
    except Exception as exc:
        logger.warning(
            "financials_service persist failed ticker=%s err=%s", ticker, exc
        )
        await db.rollback()
