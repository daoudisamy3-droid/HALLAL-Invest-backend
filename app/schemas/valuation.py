"""Pydantic schemas for the VALUATION endpoint (Step 4.5).

Spec authority: §4.3 (4 méthodes Fair Value) and §4.3.7 (API output).
Verdict strings strict FR per user validation (Q3 plan).

V1 limitation: 3 of 4 methods need data sources we haven't integrated
yet (live price + historical multiples + sector medians + analyst
consensus). They surface as ``available: false`` with explicit reasons.
Cf. ``docs/VALUATION_PARTIAL_METHODS.md``.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


ValuationVerdict = Literal["OUI", "OUI_NEUTRE", "NON", "INDÉTERMINÉ"]
ValuationConfidence = Literal["HIGH", "MEDIUM", "LOW", "N/A"]
ValuationMethodName = Literal[
    "vs_historical_5y",
    "vs_sector",
    "graham_number",
    "analyst_target",
]


class MethodResult(BaseModel):
    """One of the 4 valuation methods' outcome."""

    model_config = ConfigDict(extra="allow")

    available: bool
    fair_value: Decimal | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = Field(
        None,
        description="Populated when available=False to explain why.",
    )


class ValuationReport(BaseModel):
    """Response of GET /api/v1/valuation/{symbol} (spec §4.3.7).

    Verdict semantics:
      - OUI         : ratio < 0.95 (sous-évaluée OU légère décote)
      - OUI_NEUTRE  : 0.95 ≤ ratio < 1.05 (juste prix)
      - NON         : ratio ≥ 1.05 (surévaluée OU fortement surévaluée)
      - INDÉTERMINÉ : < 2 méthodes disponibles, OU current_price absent,
                      OU ticker non-US (no SEC EDGAR coverage)

    In V1 (Step 4.5), only the Graham Number method is computable from
    SEC EDGAR alone; therefore most reports return INDÉTERMINÉ, but the
    Graham number itself remains exposed in ``methods.graham_number``
    as a useful conservative reference.
    """

    symbol: str
    verdict: ValuationVerdict
    label: str | None = Field(
        None,
        description=(
            'Verbose label: "SOUS-ÉVALUÉE", "JUSTE PRIX (LÉGÈRE DÉCOTE)", '
            '"JUSTE PRIX", "SURÉVALUÉE", "FORTEMENT SURÉVALUÉE", or null.'
        ),
    )
    confidence: ValuationConfidence

    # Aggregation outputs (None if < 2 methods available)
    fair_value_median: Decimal | None = None
    fair_value_mean: Decimal | None = None
    dispersion: Decimal | None = Field(
        None,
        description="(max − min) / median across available methods (0.0 - 1.0+).",
    )
    ratio_price_to_fair_value: Decimal | None = None
    current_price: Decimal | None = Field(
        None,
        description="V1: always null (no live price source integrated yet).",
    )
    n_methods: int = Field(..., description="Count of methods with available=true.")

    methods: dict[ValuationMethodName, MethodResult] = Field(default_factory=dict)

    source: Literal["computed"] = "computed"
    reason: str | None = None
    warnings: list[str] = Field(default_factory=list)
