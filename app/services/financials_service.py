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


# ─── Concept-tag fallback map (Step 8 Phase A — universal coverage) ─────────


# Each financial concept maps to an ordered list of XBRL tags to try. We
# aggregate FY entries across **all** listed tags (Step 7.3.D), and across
# **all** supported taxonomies (Step 8 Phase A: us-gaap for domestic US
# filers + ifrs-full for foreign filers using 20-F / 40-F).
#
# Coverage rationale (Phase A):
#   - Tag list is intentionally permissive. SEC EDGAR ingests dozens of
#     synonymous concepts depending on industry (Revenue vs Revenues vs
#     SalesRevenueNet vs ContractRevenue vs InterestAndDividendIncomeOperating
#     for banks), and the modern revenue tag splits into the
#     "ExcludingAssessedTax" vs "IncludingAssessedTax" variants since 2018.
#   - For each concept, we list both US-GAAP and IFRS-full variants. The
#     extractor walks all taxonomies + all tags, pools the FY entries, and
#     picks the most recent.
#
# Naming convention used inside the lists: ``"taxonomy:Tag"`` when the tag
# is taxonomy-specific (e.g. ``"ifrs-full:Revenue"``). Bare tag names default
# to US-GAAP.
_CONCEPT_TAGS: dict[str, list[str]] = {
    # ── Revenues ─────────────────────────────────────────────────────────────
    "revenues": [
        # US-GAAP modern + legacy variants
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "RevenueFromContractWithCustomerIncludingAssessedTax",
        "SalesRevenueNet",
        "SalesRevenueGoodsNet",
        "SalesRevenueServicesNet",
        "Revenue",
        # IFRS variant for 20-F / 40-F filers
        "ifrs-full:Revenue",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
        "ifrs-full:ProfitLoss",
        "ifrs-full:ProfitLossAttributableToOwnersOfParent",
    ],
    "total_assets": [
        "Assets",
        "ifrs-full:Assets",
    ],
    "long_term_debt": [
        "LongTermDebt",
        "LongTermDebtNoncurrent",
        "LongTermDebtAndCapitalLeaseObligations",
        "LongTermBorrowings",
        "ifrs-full:LongtermBorrowings",
        "ifrs-full:NoncurrentBorrowings",
    ],
    "short_term_borrowings": [
        "ShortTermBorrowings",
        "DebtCurrent",
        "ShortTermBankLoansAndNotesPayable",
        "ifrs-full:CurrentBorrowings",
        "ifrs-full:ShorttermBorrowings",
    ],
    "cash_and_equivalents": [
        "CashAndCashEquivalentsAtCarryingValue",
        "Cash",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        "ifrs-full:CashAndCashEquivalents",
    ],
    "interest_income_operating": [
        "InterestIncomeOperating",
        "InterestAndDividendIncomeOperating",
    ],
    "eps_diluted": [
        "EarningsPerShareDiluted",
        "IncomeLossFromContinuingOperationsPerDilutedShare",
        "ifrs-full:DilutedEarningsLossPerShare",
    ],
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
        "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        "ifrs-full:CashFlowsFromUsedInOperatingActivities",
    ],
    "capex": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
        "PaymentsForCapitalImprovements",
        "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
    ],
    "stockholders_equity": [
        "StockholdersEquity",
        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        "ifrs-full:Equity",
        "ifrs-full:EquityAttributableToOwnersOfParent",
    ],
    # ── Step 4 internal concepts (universal coverage extension) ──────────────
    "retained_earnings": [
        "RetainedEarningsAccumulatedDeficit",
        "ifrs-full:RetainedEarnings",
    ],
    "total_liabilities": [
        "Liabilities",
        "ifrs-full:Liabilities",
    ],
    "current_assets": [
        "AssetsCurrent",
        "ifrs-full:CurrentAssets",
    ],
    "current_liabilities": [
        "LiabilitiesCurrent",
        "ifrs-full:CurrentLiabilities",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest",
        "IncomeLossFromContinuingOperationsBeforeIncomeTaxesMinorityInterestAndIncomeLossFromEquityMethodInvestments",
        "ifrs-full:ProfitLossFromOperatingActivities",
    ],
    "gross_profit": [
        "GrossProfit",
        "ifrs-full:GrossProfit",
    ],
    "receivables": [
        "AccountsReceivableNetCurrent",
        "ReceivablesNetCurrent",
        "AccountsAndOtherReceivablesNetCurrent",
        "ifrs-full:CurrentTradeReceivables",
    ],
    "shares_outstanding": [
        "CommonStockSharesOutstanding",
        "EntityCommonStockSharesOutstanding",
        "WeightedAverageNumberOfDilutedSharesOutstanding",
        "ifrs-full:NumberOfSharesOutstanding",
    ],
    "buybacks": [
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfEquity",
        "ifrs-full:PaymentsForSharesIssued",
    ],
    "r_and_d": [
        "ResearchAndDevelopmentExpense",
        "ResearchAndDevelopmentExpenseExcludingAcquiredInProcessCost",
        "ifrs-full:ResearchAndDevelopmentExpense",
    ],
}

# Most concepts are USD; EPS is reported as USD/shares, shares as a count.
_CONCEPT_UNITS: dict[str, str] = {
    "eps_diluted": "USD/shares",
    "shares_outstanding": "shares",
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


# ─── Annual entry detection (Step 8 Phase A — universal coverage) ───────────


# Foreign filers (20-F, 40-F) report on SEC EDGAR but their entries don't
# always carry ``fp="FY"``. We accept any entry whose form is in the
# annual-form whitelist AND whose period covers ~12 months (start→end).
# Both US domestic 10-K filings and foreign 20-F / 40-F annual reports are
# normalised to the same "annual entry" abstraction.
_ANNUAL_FORMS: frozenset[str] = frozenset({
    "10-K", "10-K/A", "10-KSB", "10-KSB/A",      # US domestic
    "20-F", "20-F/A",                              # foreign private issuers
    "40-F", "40-F/A",                              # Canadian filers (MJDS)
})

_ANNUAL_TAXONOMIES: tuple[str, ...] = ("us-gaap", "ifrs-full")

# Annual period tolerance: between 320 and 400 days (covers 52-week fiscal
# years + leap-year drift + reporting-week quirks).
_ANNUAL_MIN_DAYS: int = 320
_ANNUAL_MAX_DAYS: int = 400


def _is_annual_entry(e: dict[str, Any]) -> bool:
    """Permissive detection: US 10-K via ``fp == 'FY'`` OR foreign 20-F/40-F
    via form match + ~12 month period.

    Why both paths? SEC EDGAR's ``fp`` field is reliable for US domestic
    filings but is sometimes ``null`` or quarterly-coded for foreign
    private issuers even on annual filings. Using ``end - start`` duration
    as a fallback heuristic gives us robust annual detection across both
    cohorts.
    """
    if not isinstance(e, dict) or not e.get("end"):
        return False
    if e.get("fp") == "FY":
        return True
    form = e.get("form")
    if isinstance(form, str) and form in _ANNUAL_FORMS:
        # Validate period duration: must be ~1 year.
        start = e.get("start")
        end = e.get("end")
        if isinstance(start, str) and isinstance(end, str):
            try:
                d_start = date.fromisoformat(start)
                d_end = date.fromisoformat(end)
                delta_days = (d_end - d_start).days
                if _ANNUAL_MIN_DAYS <= delta_days <= _ANNUAL_MAX_DAYS:
                    return True
            except ValueError:
                pass
    return False


def _walk_concept_entries(
    facts: dict[str, Any], tag_specs: list[str], unit: str
) -> list[dict[str, Any]]:
    """Pool entries for every (taxonomy, tag) combination listed in
    ``tag_specs``, filtering to annual entries only.

    A ``tag_specs`` element may be either:
      - ``"Tag"`` → search under all taxonomies in :data:`_ANNUAL_TAXONOMIES`
      - ``"taxonomy:Tag"`` → search under that specific taxonomy only

    Returns a flat list of XBRL entry dicts. Duplicates across taxonomies
    are tolerated and resolved by the caller (latest ``filed`` wins).
    """
    facts_root = facts.get("facts")
    if not isinstance(facts_root, dict):
        return []

    pool: list[dict[str, Any]] = []
    for spec in tag_specs:
        if ":" in spec:
            taxonomy, tag = spec.split(":", 1)
            taxonomies: tuple[str, ...] = (taxonomy,)
        else:
            tag = spec
            taxonomies = _ANNUAL_TAXONOMIES

        for taxonomy in taxonomies:
            tax_data = facts_root.get(taxonomy)
            if not isinstance(tax_data, dict):
                continue
            tag_data = tax_data.get(tag)
            if not isinstance(tag_data, dict):
                continue
            units = tag_data.get("units", {})
            if not isinstance(units, dict):
                continue
            entries = units.get(unit)
            if not isinstance(entries, list):
                continue
            for e in entries:
                if _is_annual_entry(e):
                    pool.append(e)
    return pool


def _extract_latest_annual(
    facts: dict[str, Any],
    tags: list[str],
    unit: str,
) -> dict[str, Any] | None:
    """Return the latest annual entry across ALL tags + taxonomies.

    Step 8 Phase A: walks both ``us-gaap`` and ``ifrs-full`` taxonomies
    so foreign filers (20-F / 40-F using IFRS) are covered without code
    forks. Uses :func:`_is_annual_entry` for permissive annual detection
    (US ``fp="FY"`` OR foreign annual-form + ~12 month period).

    Restatements (same ``end`` across multiple ``filed``) are resolved by
    sort: ``end`` desc, ``filed`` desc. Step 7.3.D fix preserved
    (aggregation across tags before picking the latest).
    """
    pool = _walk_concept_entries(facts, tags, unit)
    if not pool:
        return None
    pool.sort(
        key=lambda e: (str(e.get("end", "")), str(e.get("filed", ""))),
        reverse=True,
    )
    return pool[0]


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


# ─── Step 4 — internal helpers for score computations ───────────────────────


async def get_facts_payload(
    ticker: str,
    db: AsyncSession,
    client: SecEdgarClient,
) -> tuple[int, str, dict[str, Any]] | None:
    """Internal helper for step-4 scoring.

    Returns ``(cik, entity_name, raw_facts)`` for a US-listed ticker, or
    ``None`` if the ticker is not in SEC EDGAR's universe (non-US or
    unknown). Same cache / persistence semantics as :func:`get_financials`.

    Differs from :func:`get_financials` in that it surfaces the *raw*
    ``company_facts`` payload so the score components (Altman, Piotroski,
    Growth…) can run multi-year extractions. Not exposed as a public
    HTTP endpoint.

    Raises:
        SECEdgarError: on transient upstream failure (5xx, timeout).
    """
    tk = ticker.strip().upper()

    # Cache-first path — same lookup as _read_cache, returning the raw blob.
    cutoff = datetime.now(tz=timezone.utc) - timedelta(
        hours=settings.FINANCIALS_CACHE_TTL_HOURS
    )
    stmt = (
        select(FinancialsCache)
        .where(FinancialsCache.ticker == tk)
        .where(FinancialsCache.fetched_at >= cutoff)
        .order_by(FinancialsCache.fetched_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is not None:
        entity = str(row.facts_json.get("entityName", "")) if isinstance(row.facts_json, dict) else ""
        return (row.cik, entity, row.facts_json or {})

    # Cache miss — lookup CIK + fetch facts. SECEdgarError propagates.
    cik_info = await client.lookup_cik(tk)
    if cik_info is None:
        return None

    cik, entity_name = cik_info
    facts = await client.get_company_facts(cik)
    if facts is None:
        return None

    entity = str(facts.get("entityName", "")) or entity_name
    # Persist for next call (same TTL behavior as get_financials).
    await _persist(db, tk, cik, facts)
    return (cik, entity, facts)


def extract_latest_annual_value(
    facts: dict[str, Any], concept_name: str
) -> Decimal | None:
    """Latest FY value for a concept (single Decimal), with multi-tag fallback.

    Used by Altman (single-year balance sheet), Growth (latest endpoint of
    CAGR window), Capital D3 (latest CapEx + R&D + Revenue), etc.
    """
    if concept_name not in _CONCEPT_TAGS:
        raise KeyError(f"unknown concept_name: {concept_name}")
    unit = _CONCEPT_UNITS.get(concept_name, "USD")
    entry = _extract_latest_annual(facts, _CONCEPT_TAGS[concept_name], unit)
    if entry is None:
        return None
    return _to_decimal(entry.get("val"))


def extract_n_year_annuals(
    facts: dict[str, Any],
    concept_name: str,
    n: int = 5,
) -> list[tuple[date, Decimal]]:
    """Return up to ``n`` most recent FY entries for ``concept_name``.

    Returned list is sorted **most-recent-first**: ``[(period_end_y0, val_y0),
    (period_end_y1, val_y1), …]``. We aggregate FY entries across **all**
    concept tags listed in ``_CONCEPT_TAGS[concept_name]`` rather than
    short-circuiting on the first tag that yields anything (Step 7.3.D
    bug fix: Apple stopped using legacy ``Revenues`` ~FY 2018 and migrated
    to ``RevenueFromContractWithCustomerExcludingAssessedTax`` — a
    first-tag-wins fallback would silently return the stale FY 2018
    series).

    Restatements (same ``end`` across multiple ``accn`` or across tags)
    are de-duplicated by keeping the entry with the latest ``filed``.

    Used by Piotroski (YoY criteria → need 2 years), Growth (CAGR 3y → 4
    years), Capital D1 (buybacks 3y → 3 years), Fraud Sig 1/2 (3 years).
    """
    if concept_name not in _CONCEPT_TAGS:
        raise KeyError(f"unknown concept_name: {concept_name}")
    unit = _CONCEPT_UNITS.get(concept_name, "USD")

    pool = _walk_concept_entries(facts, _CONCEPT_TAGS[concept_name], unit)
    if not pool:
        return []

    # Group by end date, pick latest filed for each (handles restatements
    # and same-end overlaps between legacy + modern tags).
    by_end: dict[str, dict[str, Any]] = {}
    for e in pool:
        end = str(e.get("end", ""))
        prev = by_end.get(end)
        if prev is None or str(e.get("filed", "")) > str(prev.get("filed", "")):
            by_end[end] = e

    sorted_entries = sorted(by_end.values(), key=lambda e: str(e["end"]), reverse=True)
    result: list[tuple[date, Decimal]] = []
    for e in sorted_entries[:n]:
        d = _to_date(e.get("end"))
        v = _to_decimal(e.get("val"))
        if d is not None and v is not None:
            result.append((d, v))
    return result
