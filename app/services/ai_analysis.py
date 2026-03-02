import json

from app.core.logging import logger
from app.integration.anthropic_client import analyze_with_claude
from app.models.schemas import (
    Fundamentals,
    Technicals,
    ShariahScreening,
    AIVerdict,
    AISection,
    EntryZone,
)


def _build_prompt(
    symbol: str,
    fundamentals: Fundamentals,
    technicals: Technicals,
    shariah: ShariahScreening,
) -> str:
    return f"""Analyze the following equity data for **{symbol}** and return your analysis as a JSON object.

## Fundamentals
- Name: {fundamentals.name}
- Sector: {fundamentals.sector} | Industry: {fundamentals.industry}
- Market Cap: {fundamentals.market_cap}
- P/E: {fundamentals.pe_ratio} | PEG: {fundamentals.peg_ratio}
- ROE: {fundamentals.roe}% | ROA: {fundamentals.roa}%
- Net Margin: {fundamentals.net_margin}% | Gross Margin: {fundamentals.gross_margin}%
- FCF Margin: {fundamentals.fcf_margin}%
- Debt/Equity: {fundamentals.debt_to_equity}
- 3Y Revenue CAGR: {fundamentals.revenue_cagr_3y}%

## Technicals
- Current Price: {technicals.current_price}
- RSI (14d): {technicals.rsi_14}
- SMA 50: {technicals.sma_50} | SMA 200: {technicals.sma_200}
- Bollinger Bands: Upper={technicals.bollinger_bands.upper if technicals.bollinger_bands else None}, Mid={technicals.bollinger_bands.middle if technicals.bollinger_bands else None}, Lower={technicals.bollinger_bands.lower if technicals.bollinger_bands else None}
- Max Drawdown (5Y): {technicals.max_drawdown_5y}%

## Shariah Screening
- Halal Badge: {shariah.halal_badge}
- AAOIFI Level: {"PASS" if shariah.aaoifi.passed else "FAIL"}
- Strict Level: {"PASS" if shariah.strict.passed else "FAIL"}
- Summary: {shariah.summary}
- AAOIFI Ratios: {json.dumps([r.model_dump() for r in shariah.aaoifi.ratios], default=str)}
- Strict Ratios: {json.dumps([r.model_dump() for r in shariah.strict.ratios], default=str)}

## Required JSON Output Format
Return ONLY a valid JSON object with exactly this structure:
{{
  "shariah_compliance": {{
    "rating": "COMPLIANT" | "DOUBTFUL" | "NON-COMPLIANT",
    "summary": "one-paragraph assessment",
    "details": ["point 1", "point 2"]
  }},
  "company_quality": {{
    "rating": "EXCELLENT" | "GOOD" | "AVERAGE" | "POOR",
    "summary": "one-paragraph assessment",
    "details": ["point 1", "point 2"]
  }},
  "valuation": {{
    "rating": "UNDERVALUED" | "FAIR" | "OVERVALUED",
    "summary": "one-paragraph assessment",
    "details": ["point 1", "point 2"]
  }},
  "entry_timing": {{
    "rating": "FAVORABLE" | "NEUTRAL" | "UNFAVORABLE",
    "summary": "one-paragraph assessment",
    "details": ["point 1", "point 2"]
  }},
  "final_verdict": "BUY" | "WAIT" | "AVOID",
  "entry_zone": {{ "low": <float>, "high": <float> }},
  "stop_loss": <float>,
  "target_18m": <float>
}}
"""


def _parse_verdict(raw: str) -> AIVerdict:
    """Best-effort parse of Claude's JSON response into our schema."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        # Try to extract JSON from markdown fences
        import re
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if match:
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                logger.warning("Could not parse AI response as JSON")
                return AIVerdict(raw_response=raw)
        else:
            logger.warning("Could not parse AI response as JSON")
            return AIVerdict(raw_response=raw)

    def _parse_section(key: str) -> AISection | None:
        section = data.get(key)
        if not section or not isinstance(section, dict):
            return None
        return AISection(
            rating=section.get("rating"),
            summary=section.get("summary", ""),
            details=section.get("details", []),
        )

    entry_zone = None
    ez = data.get("entry_zone")
    if isinstance(ez, dict):
        entry_zone = EntryZone(low=ez.get("low"), high=ez.get("high"))

    return AIVerdict(
        shariah_compliance=_parse_section("shariah_compliance"),
        company_quality=_parse_section("company_quality"),
        valuation=_parse_section("valuation"),
        entry_timing=_parse_section("entry_timing"),
        final_verdict=data.get("final_verdict"),
        entry_zone=entry_zone,
        stop_loss=data.get("stop_loss"),
        target_18m=data.get("target_18m"),
    )


async def get_ai_verdict(
    symbol: str,
    fundamentals: Fundamentals,
    technicals: Technicals,
    shariah: ShariahScreening,
) -> AIVerdict:
    prompt = _build_prompt(symbol, fundamentals, technicals, shariah)
    logger.info("Requesting AI analysis for %s", symbol)

    raw = await analyze_with_claude(prompt)
    logger.info("Received AI response for %s (%d chars)", symbol, len(raw))

    return _parse_verdict(raw)
