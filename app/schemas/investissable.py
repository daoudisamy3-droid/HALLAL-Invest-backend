"""Pydantic schemas for the INVESTISSABLE endpoint (Step 4).

Spec authority: §4.2.4 — sortie API onglet 1. Verdict strings kept in
**French strict** ("OUI" / "NON" / "INCERTAIN") per Q10 plan; the
quality-component scores use ``float | None`` (0-100 range — float is
fine for percentages, exact Decimal arithmetic is done in the service
layer).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


InvestissableVerdict = Literal["OUI", "NON", "INCERTAIN"]
GateName = Literal["aaoifi", "altman", "fraud"]
QualityComponentName = Literal[
    "piotroski",
    "growth",
    "smart_money",
    "capital_allocation",
    "earnings_stability",
]
DataCompleteness = Literal["FULL", "PARTIAL", "INSUFFICIENT"]


class GateResult(BaseModel):
    """Generic per-gate result with verdict + open-shape details."""

    model_config = ConfigDict(extra="allow")

    verdict: Literal["PASS", "WARNING", "FAIL", "INSUFFICIENT_DATA"]
    details: dict[str, Any] = Field(default_factory=dict)
    reason: str | None = None


class QualityComponent(BaseModel):
    """A single quality-score component (0-100) with traceability details."""

    model_config = ConfigDict(extra="allow")

    score: float | None = Field(
        None,
        description="Normalised 0-100 score, or null when the component is N/A.",
    )
    available: bool = Field(
        ...,
        description="True iff the component contributed to the weighted quality.",
    )
    details: dict[str, Any] = Field(default_factory=dict)


class InvestissableReport(BaseModel):
    """Top-level response of GET /api/v1/investissable/{symbol}.

    The verdict is one of three French strings (§4.2.3 / Q10 plan):

      - ``OUI``        : all gates passed + quality score ≥ 45.
      - ``NON``        : a gate failed OR quality score < 45.
      - ``INCERTAIN``  : Altman INSUFFICIENT_DATA, quality data
                         insufficient, or non-US ticker (V1 limitation).

    ``blocked_at`` is set when a gate failure short-circuits the
    pipeline. ``data_completeness`` reflects how many components
    contributed (FULL = 5/5, PARTIAL = 1-4/5, INSUFFICIENT = 0/5 or
    non-US).
    """

    symbol: str
    verdict: InvestissableVerdict
    label: str | None = Field(
        None,
        description='Verbose label e.g. "OUI - QUALITÉ EXCELLENTE" (§4.2.3).',
    )
    quality_score: float | None = None
    blocked_at: GateName | None = None

    gates: dict[GateName, GateResult] = Field(default_factory=dict)
    quality_components: dict[QualityComponentName, QualityComponent] = Field(
        default_factory=dict,
    )

    data_completeness: DataCompleteness
    warnings: list[str] = Field(default_factory=list)
    reason: str | None = None
