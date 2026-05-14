"""Shared schema: ``CalculationDetail`` — auditable formula payload.

Spec authority: étape 8 Phase B (transparency of computations).

Every metric / verdict surfaced by the investissable + valuation
endpoints carries one of these structures so the frontend can render
an accordion with the formula, the variables it substituted, the
intermediate steps, the final result, the threshold grid, and the
human-readable interpretation. The user is expected to be able to
recompute the verdict by hand from this payload.

Design notes:

  - All numeric fields are **strings** (Pydantic Decimal serialisation)
    — never coerce to float for arithmetic on the frontend.
  - ``computation_steps`` is a flat list of human-readable strings so
    rendering stays trivial. The order matters: top-to-bottom should
    read like a hand calculation.
  - ``thresholds`` is a list of ``{label, condition}`` pairs (in
    descending order of "good news") so the frontend can highlight
    which threshold the ``result`` satisfies.
  - ``interpretation`` is the one-sentence verbal conclusion linking
    ``result`` to the matched threshold.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ThresholdSpec(BaseModel):
    """One row of the threshold grid attached to a calculation."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(..., description='ex: "SAFE", "GREY", "DISTRESS"')
    condition: str = Field(..., description='ex: "Z\'\' >= 2.6"')


class CalculationDetail(BaseModel):
    """Auditable payload describing how a single metric was computed."""

    model_config = ConfigDict(extra="forbid")

    formula: str = Field(
        ...,
        description=(
            'The literal formula applied, e.g. '
            '"Z\'\' = 6.56·(WC/TA) + 3.26·(RE/TA) + 6.72·(EBIT/TA) + 1.05·(BV/TL)".'
        ),
    )
    variables: dict[str, str | None] = Field(
        default_factory=dict,
        description=(
            'Variable values substituted into the formula, as strings to '
            'preserve Decimal precision. Use null when a variable is missing.'
        ),
    )
    intermediates: dict[str, str | None] = Field(
        default_factory=dict,
        description=(
            'Intermediate ratios computed between substitution and the final '
            'reduction. Ex: WC/TA, RE/TA, EBIT/TA, BV/TL.'
        ),
    )
    computation_steps: list[str] = Field(
        default_factory=list,
        description=(
            'Ordered, human-readable lines mimicking a hand calculation. '
            'Ex: "6.56 × 0.0353 = 0.2316".'
        ),
    )
    result: str | None = Field(
        None,
        description=(
            'Final scalar result of the calculation (string for Decimal '
            'fidelity). null when the calculation could not run.'
        ),
    )
    thresholds: list[ThresholdSpec] = Field(
        default_factory=list,
        description='Verdict grid applied to the result.',
    )
    interpretation: str | None = Field(
        None,
        description=(
            'One-sentence conclusion linking result to the matched threshold.'
        ),
    )
