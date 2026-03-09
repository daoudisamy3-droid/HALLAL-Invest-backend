"""
Business Intelligence Service — Gemini-powered strategic analysis.

Uses Google Gemini to generate high-impact company diagnostics:
  - Revenue mix (product segments with percentages)
  - Competitors (leader / challenger tagging)
  - Moat pillars (max 3 key competitive advantages)
  - SWOT (strengths + weaknesses)

Falls back to empty/neutral data on any Gemini failure.
"""

import json
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import logger
from app.integration.yfinance_client import get_ticker_info


# ── Gemini REST endpoint ──────────────────────────────────────────

_GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent"
)

_GEMINI_TIMEOUT = 30.0  # seconds


# ── Prompt ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
Tu es un analyste financier senior. À partir de la fiche entreprise ci-dessous,
produis un diagnostic stratégique structuré.

RÈGLES STRICTES :
- Réponds UNIQUEMENT en JSON valide, sans markdown, sans commentaire.
- Respecte EXACTEMENT le schéma ci-dessous.
- revenue_mix : entre 3 et 6 segments. La somme des pourcentages doit ≈ 100.
- competitors : entre 3 et 5 concurrents. tag = "leader" ou "challenger".
- moat : exactement 3 avantages compétitifs clés (phrases courtes).
- swot.strengths : exactement 2 items {label, detail}.
- swot.weaknesses : exactement 2 items {label, detail}.

SCHÉMA JSON :
{
  "revenue_mix": [{"segment": "...", "pct": 45.0}, ...],
  "competitors": [{"name": "...", "tag": "leader"}, ...],
  "moat": ["...", "...", "..."],
  "swot": {
    "strengths": [{"label": "...", "detail": "..."}, ...],
    "weaknesses": [{"label": "...", "detail": "..."}, ...]
  }
}
"""


def _build_user_prompt(
    symbol: str,
    company_name: Optional[str],
    sector: Optional[str],
    industry: Optional[str],
    description: Optional[str],
) -> str:
    """Build the user message with company context."""
    parts = [f"Symbole : {symbol}"]
    if company_name:
        parts.append(f"Nom : {company_name}")
    if sector:
        parts.append(f"Secteur : {sector}")
    if industry:
        parts.append(f"Industrie : {industry}")
    if description:
        # Truncate very long descriptions to stay within token limits
        desc = description[:2000] if len(description) > 2000 else description
        parts.append(f"Description :\n{desc}")
    return "\n".join(parts)


# ── Gemini API call ───────────────────────────────────────────────

async def _call_gemini(prompt: str) -> Optional[dict]:
    """
    Call Gemini API and parse the JSON response.
    Returns parsed dict or None on any failure.
    """
    settings = get_settings()
    api_key = settings.gemini_api_key
    if not api_key:
        logger.warning("GEMINI_API_KEY not configured — skipping Gemini call")
        return None

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": _SYSTEM_PROMPT},
                    {"text": prompt},
                ]
            }
        ],
        "generationConfig": {
            "temperature": 0.2,
            "maxOutputTokens": 1500,
            "responseMimeType": "application/json",
        },
    }

    try:
        async with httpx.AsyncClient(timeout=_GEMINI_TIMEOUT) as client:
            resp = await client.post(
                f"{_GEMINI_URL}?key={api_key}",
                json=payload,
            )

        if resp.status_code != 200:
            logger.error(
                "Gemini API returned HTTP %d: %s", resp.status_code, resp.text[:300]
            )
            return None

        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]

        # Strip markdown fences if Gemini wraps the JSON
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()

        return json.loads(text)

    except (json.JSONDecodeError, KeyError, IndexError) as exc:
        logger.error("Gemini response parsing failed: %s", exc)
        return None
    except Exception as exc:
        logger.error("Gemini call failed: %s", exc)
        return None


# ── Empty fallback ────────────────────────────────────────────────

def _empty_strategy(symbol: str) -> dict[str, Any]:
    """Return a clean empty strategy dict (Gemini failure fallback)."""
    return {
        "symbol": symbol,
        "revenue_mix": [],
        "competitors": [],
        "moat": [],
        "swot": {"strengths": [], "weaknesses": []},
        "source": "fallback",
    }


# ── Main Entry Point ─────────────────────────────────────────────

async def get_company_strategy(symbol: str) -> dict[str, Any]:
    """
    Generate a Gemini-powered strategic diagnostic for a company.

    Pipeline:
        1. Fetch company info from yfinance (name, sector, description)
        2. Call Gemini with structured prompt
        3. Parse and validate the JSON response
        4. On any failure → return clean empty data

    Args:
        symbol: Ticker symbol (e.g. "AAPL").

    Returns:
        {
            "symbol": str,
            "revenue_mix": [{"segment": str, "pct": float}, ...],
            "competitors": [{"name": str, "tag": str}, ...],
            "moat": [str, str, str],
            "swot": {"strengths": [...], "weaknesses": [...]},
            "source": "gemini" | "fallback",
        }
    """
    symbol = symbol.upper().strip()
    logger.info("=== BUSINESS INTELLIGENCE START for %s ===", symbol)

    # ── Step 1: Get company context from yfinance ─────────────────
    company_name = None
    sector = None
    industry = None
    description = None

    try:
        info = await get_ticker_info(symbol)
        company_name = info.get("longName") or info.get("shortName")
        sector = info.get("sector")
        industry = info.get("industry")
        description = info.get("longBusinessSummary")
    except Exception as exc:
        logger.warning("yfinance info failed for %s: %s — calling Gemini with symbol only", symbol, exc)

    if not description and not sector:
        logger.warning("No company context available for %s — returning fallback", symbol)
        result = _empty_strategy(symbol)
        result["flags"] = ["NO_COMPANY_DATA"]
        return result

    # ── Step 2: Call Gemini ────────────────────────────────────────
    user_prompt = _build_user_prompt(symbol, company_name, sector, industry, description)
    gemini_result = await _call_gemini(user_prompt)

    if gemini_result is None:
        logger.warning("Gemini failed for %s — returning fallback", symbol)
        result = _empty_strategy(symbol)
        result["flags"] = ["GEMINI_UNAVAILABLE"]
        return result

    # ── Step 3: Validate & normalize ──────────────────────────────
    revenue_mix = gemini_result.get("revenue_mix", [])
    if not isinstance(revenue_mix, list):
        revenue_mix = []

    competitors = gemini_result.get("competitors", [])
    if not isinstance(competitors, list):
        competitors = []

    moat = gemini_result.get("moat", [])
    if not isinstance(moat, list):
        moat = []
    moat = moat[:3]  # enforce max 3

    swot_raw = gemini_result.get("swot", {})
    if not isinstance(swot_raw, dict):
        swot_raw = {}
    swot = {
        "strengths": swot_raw.get("strengths", [])[:2],
        "weaknesses": swot_raw.get("weaknesses", [])[:2],
    }

    result = {
        "symbol": symbol,
        "company_name": company_name,
        "sector": sector,
        "industry": industry,
        "revenue_mix": revenue_mix,
        "competitors": competitors,
        "moat": moat,
        "swot": swot,
        "source": "gemini",
    }

    logger.info(
        "=== BUSINESS INTELLIGENCE END for %s: %d segments, %d competitors, %d moat points ===",
        symbol, len(revenue_mix), len(competitors), len(moat),
    )

    return result
