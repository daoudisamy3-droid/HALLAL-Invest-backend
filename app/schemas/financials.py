"""Pydantic schemas for the SEC EDGAR financials endpoint.

Spec authority:
  - §3.2.2 — SEC EDGAR endpoints, concept tags, TTL.
  - master-prompt §9.2 étape 2 — livrables.

Contract design (Q3 plan): mirror the ShariahReport pattern — the
endpoint always returns 200 and the verdict lives in the payload.
This keeps the API surface uniform for callers (Step 6 synthesis will
fan out to several endpoints in parallel and benefits from a single
error-handling shape).

Step-2 deliberately exposes ONLY raw XBRL values (no derivatives like
TTM, ratios, total_debt sum). Derived metrics are Step 4 (SCORE) and
beyond.
"""

from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


FinancialsVerdict = Literal["AVAILABLE", "NOT_COVERED", "ERROR"]
FinancialsSource = Literal["SEC EDGAR API", "cache (financials_cache)"]


class FinancialsSnapshot(BaseModel):
    """Latest annual snapshot extracted from SEC EDGAR for a US-listed ticker.

    Verdict semantics:
      - AVAILABLE   : metadata + 11 concept values populated (some may be
                      None if the concept is not reported by the company)
      - NOT_COVERED : ticker not in SEC EDGAR universe (non-US, unknown,
                      or de-listed)
      - ERROR       : transient upstream failure (timeout / 5xx / network)
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    ticker: str = Field(..., description="Ticker normalised to uppercase")
    cik: int | None = Field(
        None, description="SEC Central Index Key, None if NOT_COVERED"
    )
    entity_name: str | None = None
    verdict: FinancialsVerdict

    # ── Anchor filing metadata (None unless AVAILABLE) ──────────────────────
    fiscal_year: int | None = None
    period_end: date | None = None
    form: str | None = Field(None, description='e.g. "10-K"')
    accession_number: str | None = None
    filed: date | None = None

    # ── 11 critical XBRL concepts §3.2.2 (RAW values, no derivation) ────────
    # Values are Decimal for exact representation; serialised to JSON as
    # strings. All concepts are USD except eps_diluted (USD per share).
    revenues: Decimal | None = None
    net_income: Decimal | None = None
    total_assets: Decimal | None = None
    long_term_debt: Decimal | None = None
    short_term_borrowings: Decimal | None = None
    cash_and_equivalents: Decimal | None = None
    interest_income_operating: Decimal | None = None
    eps_diluted: Decimal | None = None  # unit: USD/shares
    operating_cash_flow: Decimal | None = None
    capex: Decimal | None = None
    stockholders_equity: Decimal | None = None

    # ── Provenance / cache metadata (mirror of ShariahReport contract) ──────
    source: FinancialsSource
    cached: bool = False
    cache_age_hours: float | None = None

    # ── Human-readable rationale ────────────────────────────────────────────
    # AVAILABLE   → typically None (entity name is in entity_name)
    # NOT_COVERED → "AIXA.DE non couvert par SEC EDGAR (probablement non-US)"
    # ERROR       → upstream failure message
    reason: str | None = None
