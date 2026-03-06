import json
import re

from app.core.logging import logger
from app.integration.anthropic_client import analyze_with_claude
from app.models.schemas import (
    Fundamentals,
    Technicals,
    ShariahScreening,
    AICommentary,
    AISection,
    EntryZone,
)


def _build_prompt(
    symbol: str,
    fundamentals: Fundamentals,
    technicals: Technicals,
    shariah: ShariahScreening,
) -> str:
    return f"""Voici les données BRUTES extraites des API financières pour l'action **{symbol}**.
Tu dois UNIQUEMENT commenter et analyser ces chiffres. Tu ne dois JAMAIS inventer, estimer ou modifier un chiffre.
Si une donnée est null/None, indique explicitement « donnée non disponible » — ne la remplace pas.

Rédige INTÉGRALEMENT en français. Conserve les acronymes techniques en anglais (P/E, PEG, ROE, RSI, SMA, etc.).

## Fondamentaux (données brutes yfinance)
- Nom : {fundamentals.name}
- Secteur : {fundamentals.sector} | Industrie : {fundamentals.industry}
- Capitalisation boursière : {fundamentals.market_cap}
- P/E (trailing) : {fundamentals.trailing_pe} | P/E (forward) : {fundamentals.forward_pe}
- PEG : {fundamentals.peg_ratio}
- Dividend Yield : {fundamentals.dividend_yield}
- ROE : {fundamentals.return_on_equity}
- Gross Margins : {fundamentals.gross_margins}
- Debt/Equity : {fundamentals.debt_to_equity}
- Total Debt : {fundamentals.total_debt}
- Total Revenue : {fundamentals.total_revenue}
- Free Cash Flow : {fundamentals.free_cashflow}

## Analyse technique (calculée à partir de données OHLC brutes)
- Cours actuel : {technicals.current_price}
- RSI (14j) : {technicals.rsi_14}
- SMA 50 : {technicals.sma_50} | SMA 200 : {technicals.sma_200}
- Bandes de Bollinger : Haute={technicals.bollinger_bands.upper if technicals.bollinger_bands else None}, Médiane={technicals.bollinger_bands.middle if technicals.bollinger_bands else None}, Basse={technicals.bollinger_bands.lower if technicals.bollinger_bands else None}
- Drawdown max (5 ans) : {technicals.max_drawdown_5y}%

## Screening Shariah (ratios calculés à partir des données brutes)
- Badge : {shariah.halal_badge}
- AAOIFI : {shariah.aaoifi.passed} | Strict : {shariah.strict.passed}
- Résumé : {shariah.summary}
- Ratios AAOIFI : {json.dumps([r.model_dump() for r in shariah.aaoifi.ratios], default=str)}
- Ratios Strict : {json.dumps([r.model_dump() for r in shariah.strict.ratios], default=str)}

## RÈGLES STRICTES
1. Ne modifie AUCUN chiffre ci-dessus dans ton analyse.
2. Si une donnée est None/null, écris « N/A » — ne l'estime jamais.
3. Base ton commentaire EXCLUSIVEMENT sur les données fournies.

## Format JSON attendu
Renvoie UNIQUEMENT un objet JSON valide :
{{
  "shariah_compliance": {{
    "rating": "COMPLIANT" | "DOUBTFUL" | "NON-COMPLIANT" | "INCONCLUSIVE",
    "summary": "commentaire en français basé sur les ratios ci-dessus",
    "details": ["point 1", "point 2"]
  }},
  "company_quality": {{
    "rating": "EXCELLENT" | "GOOD" | "AVERAGE" | "POOR",
    "summary": "commentaire en français",
    "details": ["point 1", "point 2"]
  }},
  "valuation": {{
    "rating": "UNDERVALUED" | "FAIR" | "OVERVALUED",
    "summary": "commentaire en français",
    "details": ["point 1", "point 2"]
  }},
  "entry_timing": {{
    "rating": "FAVORABLE" | "NEUTRAL" | "UNFAVORABLE",
    "summary": "commentaire en français",
    "details": ["point 1", "point 2"]
  }},
  "final_verdict": "BUY" | "WAIT" | "AVOID",
  "entry_zone": {{ "low": <float>, "high": <float> }},
  "stop_loss": <float>,
  "target_18m": <float>
}}
"""


def _parse_commentary(raw: str) -> AICommentary:
    """Best-effort parse of Claude's JSON response into our schema."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
        if match:
            try:
                data = json.loads(match.group(1))
            except json.JSONDecodeError:
                logger.warning("Could not parse AI response as JSON")
                return AICommentary(raw_response=raw)
        else:
            logger.warning("Could not parse AI response as JSON")
            return AICommentary(raw_response=raw)

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

    return AICommentary(
        shariah_compliance=_parse_section("shariah_compliance"),
        company_quality=_parse_section("company_quality"),
        valuation=_parse_section("valuation"),
        entry_timing=_parse_section("entry_timing"),
        final_verdict=data.get("final_verdict"),
        entry_zone=entry_zone,
        stop_loss=data.get("stop_loss"),
        target_18m=data.get("target_18m"),
    )


async def get_ai_commentary(
    symbol: str,
    fundamentals: Fundamentals,
    technicals: Technicals,
    shariah: ShariahScreening,
) -> AICommentary:
    prompt = _build_prompt(symbol, fundamentals, technicals, shariah)
    logger.info("Requesting AI commentary for %s", symbol)

    raw = await analyze_with_claude(prompt)
    logger.info("Received AI commentary for %s (%d chars)", symbol, len(raw))

    return _parse_commentary(raw)
