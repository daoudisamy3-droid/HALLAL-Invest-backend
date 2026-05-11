"""Pydantic schemas for the Shariah screening endpoint.

Spec authority:
  - §3.2.1 raw_ratios shape returned by Halal Terminal
  - §5.1 ShariahCustomThresholds
  - §5.2 algorithm output (verdict / failed_checks / checks /
    halal_terminal_methodology_verdicts / raw_ratios / as_of_date /
    source)

Documented deviations from §5.2 (validated extension, Step 1, Q4):
  - `cached: bool` — true when the report was served from
    `screen_history` rather than a fresh upstream call.
  - `cache_age_days: int | None` — days elapsed since the cached row
    was inserted, surfaced for UI consumers that want to show
    freshness.
  - `source` enriched: either "Halal Terminal API + custom thresholds"
    (fresh call) or "cache (screen_history)" (cache hit).
These fields are additive and never override the §5.2 contract;
downstream consumers that ignore them keep working unchanged.
"""

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


Verdict = Literal["PASS", "FAIL", "ERROR", "NOT_COVERED"]
MethodologyStatus = Literal["compliant", "non_compliant"]
CheckName = Literal[
    "debt_to_marketcap",
    "cash_to_marketcap",
    "impure_revenue_ratio",
    "interest_income_ratio",
]


class ShariahRatios(BaseModel):
    """Raw ratios returned by Halal Terminal §3.2.1.

    All fields optional: providers may omit any subset depending on
    coverage. We extract what we need; missing ratios that map to a
    bloquant check default to 0.0 in the service layer (cf. §5.2).
    """

    model_config = ConfigDict(extra="allow")

    debt_to_marketcap: float | None = None
    debt_to_assets: float | None = None
    cash_to_marketcap: float | None = None
    impure_revenue_ratio: float | None = None
    interest_income_ratio: float | None = None
    receivables_to_assets: float | None = None
    non_compliant_assets_ratio: float | None = None


class ShariahCheck(BaseModel):
    """One of the 4 bloquant checks evaluated against §5.1 thresholds."""

    value: float
    threshold: float
    passed: bool = Field(
        ...,
        alias="pass",
        serialization_alias="pass",
        description="True if value <= threshold (alias `pass` matches §5.2)",
    )

    model_config = ConfigDict(populate_by_name=True)


class MethodologyVerdict(BaseModel):
    """Verdict from one of the 5 published methodologies (§3.2.1)."""

    status: MethodologyStatus
    failed_ratios: list[str] = Field(default_factory=list)


class ShariahReport(BaseModel):
    """Response of `GET /api/v1/shariah/{symbol}`.

    Mirrors §5.2 output, extended with cache metadata (see module
    docstring).
    """

    symbol: str = Field(..., description="Ticker normalised to uppercase")
    verdict: Verdict

    # §5.2 — list of failed bloquant checks (empty if verdict == PASS)
    failed_checks: list[CheckName] = Field(default_factory=list)

    # §5.2 — 4 bloquant checks evaluated against §5.1 thresholds
    checks: dict[CheckName, ShariahCheck] = Field(default_factory=dict)

    # §3.2.1 — verdicts of the 5 published methodologies (informational)
    halal_terminal_methodology_verdicts: dict[str, MethodologyVerdict] = Field(
        default_factory=dict
    )

    # §3.2.1 — raw ratios from provider (7 ratios incl. non-bloquant ones)
    raw_ratios: ShariahRatios = Field(default_factory=ShariahRatios)

    # §3.2.1 — date the financial data is current as of (per provider)
    as_of_date: date | None = None

    # §5.2 — provenance
    source: str

    # Cache metadata (extension, see module docstring)
    cached: bool = False
    cache_age_days: int | None = None

    # Human-readable error message when verdict in {"ERROR", "NOT_COVERED"}
    reason: str | None = None
