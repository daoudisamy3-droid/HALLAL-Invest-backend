"""
Strategic Analysis Engine — yfinance Data Only (Gemini disabled).

Pipeline:
  1. Aggregate data exclusively from yfinance (single Ticker object)
  2. Map longBusinessSummary → identity_flash (Pitch Stratégique)
  3. Return raw data immediately — no LLM latency
"""

import os
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logging import logger
from app.integration.yfinance_client import get_strategic_data
from app.models.schemas import (
    MoatPillar,
    SWOT,
    SWOTItem,
    StrategicAnalysis,
)


# ── Helpers ──────────────────────────────────────────────────────

def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _fmt_large(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= 1e12:
        return f"{sign}{abs_val / 1e12:.2f}T"
    if abs_val >= 1e9:
        return f"{sign}{abs_val / 1e9:.2f}B"
    if abs_val >= 1e6:
        return f"{sign}{abs_val / 1e6:.2f}M"
    return f"{sign}{abs_val:,.0f}"


def _extract_identity_flash(description: Optional[str]) -> Optional[str]:
    """Extract first 3 sentences from longBusinessSummary as Pitch Stratégique."""
    if not description:
        return None
    sentences = [s.strip() for s in description.replace("\n", " ").split(".") if s.strip()]
    if not sentences:
        return None
    return ". ".join(sentences[:3]) + "."


# ── Main Entry Point ─────────────────────────────────────────────

async def run_strategic_analysis(symbol: str) -> StrategicAnalysis:
    """
    Return yfinance raw data immediately.
    Gemini is disabled — no LLM call, no latency, no quota issues.
    """
    logger.info("=== STRATEGIC ANALYSIS START for %s (yfinance only) ===", symbol)

    raw = await get_strategic_data(symbol)
    flags: list[str] = []

    description = raw.get("business_description")
    if not description:
        flags.append("DESCRIPTION_UNAVAILABLE")

    identity_flash = _extract_identity_flash(description)

    market_cap = _safe_float(raw.get("market_cap"))
    revenue_mix = raw.get("revenue_mix", {})

    # Moat & SWOT: placeholder pending LLM reactivation
    _PENDING = "Données techniques en cours de calcul"
    moat_pillars = [
        MoatPillar(name="Pricing Power", score=0, comment=_PENDING),
        MoatPillar(name="Switching Cost", score=0, comment=_PENDING),
        MoatPillar(name="Network Effect", score=0, comment=_PENDING),
        MoatPillar(name="Intangible Assets", score=0, comment=_PENDING),
    ]
    swot = SWOT(
        strengths=[
            SWOTItem(label="En attente", detail=_PENDING),
            SWOTItem(label="En attente", detail=_PENDING),
        ],
        weaknesses=[
            SWOTItem(label="En attente", detail=_PENDING),
            SWOTItem(label="En attente", detail=_PENDING),
        ],
    )

    result = StrategicAnalysis(
        symbol=symbol,
        company_name=raw.get("company_name"),
        business_description=description,
        sector=raw.get("sector"),
        industry=raw.get("industry"),
        country=raw.get("country"),
        market_cap_display=_fmt_large(market_cap),
        product_segments=revenue_mix,
        geo_segments={},
        identity_flash=identity_flash,
        moat_pillars=moat_pillars,
        moat_average=None,
        swot=swot,
        flags=flags,
        source="yfinance",
        cached_at=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        "=== STRATEGIC ANALYSIS END for %s: identity=%d chars, segments=%d, flags=%s ===",
        symbol, len(identity_flash or ""), len(revenue_mix), flags,
    )
    return result
