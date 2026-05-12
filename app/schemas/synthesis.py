"""Pydantic schemas for GET /api/v1/synthesis/{symbol} (Step 6, §4.4).

Spec authority: finterminal-spec.md §4.4 (Couche synthèse 3 onglets) +
§4.5 (UI bandeau).

Design rationale (Q1 plan):
  - Each layer (halal / investissable / valuation) is summarised to a
    minimal struct (verdict + label + available + error). The frontend
    banner only needs those 4 fields.
  - Detail panels (existing endpoints) keep their own contracts; we
    don't duplicate the full ShariahReport / InvestissableReport /
    ValuationReport inside the synthesis payload.
  - ``overall_verdict`` cascades: Halal first (ethical gate), then
    Investissable, then Valuation. See ``synthesis_service.aggregate``
    for the exact rules.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


OverallVerdict = Literal[
    "INVESTABLE",        # all 3 layers green
    "NOT_INVESTABLE",    # investissable says NO (gate fail or quality < 45)
    "REQUIRES_REVIEW",   # one layer uncertain / data missing / non-US
    "BLOCKED",           # halal FAIL (ethical hard stop)
]


class SynthesisLayerStatus(BaseModel):
    """Compact per-layer status surfaced in the synthesis banner.

    ``verdict`` is the raw string from the underlying layer
    (e.g. "PASS"/"FAIL" for Halal, "OUI"/"NON"/"INCERTAIN" for
    Investissable, "OUI"/"NON"/"OUI_NEUTRE"/"INDÉTERMINÉ" for
    Valuation). The frontend maps this onto colour + icon.
    """

    verdict: str
    label: str | None = Field(
        None,
        description=(
            "Verbose label when the underlying layer publishes one "
            '(e.g. "QUALITÉ EXCELLENTE", "SOUS-ÉVALUÉE"). None otherwise.'
        ),
    )
    available: bool = Field(
        ...,
        description=(
            "False when the underlying service raised an exception. "
            "In that case ``verdict`` carries the catch-all string "
            '"ERROR" and ``error`` holds the short reason.'
        ),
    )
    error: str | None = Field(
        None,
        description="Short error message; populated iff available=false.",
    )


class SynthesisReport(BaseModel):
    """Response of GET /api/v1/synthesis/{symbol}.

    Always returns 200; verdict is in the body (consistent with the
    other 4 endpoints).
    """

    symbol: str
    overall_verdict: OverallVerdict
    overall_label: str = Field(
        ...,
        description=(
            "Human-readable French phrase composed from the 3 layer "
            "labels — e.g. 'INVESTISSABLE — QUALITÉ EXCELLENTE — "
            "JUSTE PRIX' or 'À VÉRIFIER MANUELLEMENT'."
        ),
    )
    halal: SynthesisLayerStatus
    investissable: SynthesisLayerStatus
    valuation: SynthesisLayerStatus
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(
        default_factory=list,
        description=(
            "Layer-level errors. Populated when at least one of the 3 "
            "underlying services raised."
        ),
    )
    computed_at: datetime
