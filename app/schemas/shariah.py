"""Pydantic schemas for the Shariah screening endpoint.

Spec authority:
  - §3.2.1 raw_ratios shape returned by Halal Terminal
  - §5.1 ShariahCustomThresholds
  - §5.2 algorithm output (verdict / failed_checks / checks /
    halal_terminal_methodology_verdicts / raw_ratios / as_of_date /
    source)

Free-tier deviation (Step 1.5 refactor — see
``docs/SHARIAH_FREE_TIER_DEVIATION.md``):
  - The free tier of Halal Terminal does NOT expose `ratios` —
    we consume the provider's aggregate verdict instead. As a
    consequence `checks`, `methodology_verdicts` and `raw_ratios`
    are empty in every fresh response. The schema keeps them
    (as empty dicts / a defaulted ShariahRatios) so the frontend
    contract stays stable and we can re-populate them later when
    the upgrade or alternate source unlocks the raw ratios.

Documented extensions of §5.2 (validated Step 1, Q4):
  - `cached: bool` — true when the report was served from
    `screen_history` rather than a fresh upstream call.
  - `cache_age_days: int | None` — days elapsed since the cached row
    was inserted.
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
Source = Literal[
    "Halal Terminal API (aggregate verdict)",
    "Halal Terminal API (error)",
    "cache (screen_history)",
]


class ShariahRatios(BaseModel):
    """Raw ratios returned by Halal Terminal §3.2.1.

    All fields optional: providers may omit any subset depending on
    coverage. The free tier omits all of them — see schema docstring.
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
    """One of the 4 bloquant checks evaluated against §5.1 thresholds.

    Populated only when the upstream surfaces raw ratios. Empty in
    free-tier mode — see schema docstring.
    """

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
    docstring). In free-tier mode, the 4-checks / methodology /
    raw_ratios fields are empty by design.
    """

    symbol: str = Field(..., description="Ticker normalised to uppercase")
    verdict: Verdict

    # §5.2 — list of failed bloquant checks (empty in free-tier mode)
    failed_checks: list[CheckName] = Field(default_factory=list)

    # §5.2 — 4 bloquant checks evaluated against §5.1 thresholds
    # Empty {} in free-tier mode (no raw ratios available)
    checks: dict[CheckName, ShariahCheck] = Field(default_factory=dict)

    # §3.2.1 — verdicts of the 5 published methodologies (informational)
    # Empty {} in free-tier mode
    halal_terminal_methodology_verdicts: dict[str, MethodologyVerdict] = Field(
        default_factory=dict
    )

    # §3.2.1 — raw ratios from provider (7 ratios incl. non-bloquant ones)
    # All None fields in free-tier mode
    raw_ratios: ShariahRatios = Field(default_factory=ShariahRatios)

    # §3.2.1 — date the financial data is current as of (per provider)
    # Always None in free-tier mode (provider does not surface it)
    as_of_date: date | None = None

    # §5.2 — provenance (strict Literal for OpenAPI clarity)
    source: Source

    # Cache metadata (extension, see module docstring)
    cached: bool = False
    cache_age_days: int | None = None

    # Human-readable rationale.
    # PASS  → optional context
    # FAIL  → which screen failed (business / financial / is_compliant)
    # ERROR → provider failure or incomplete response
    # NOT_COVERED → ticker_unknown message from provider
    reason: str | None = None

