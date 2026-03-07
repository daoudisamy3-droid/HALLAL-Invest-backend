"""
Strategic Analysis Engine — Data Aggregator + LLM Synthesis.

Pipeline:
  1. Aggregate data exclusively from yfinance (single Ticker object)
  2. Build a single structured prompt for Gemini (PE analyst persona)
  3. Parse structured JSON response → StrategicAnalysis
  4. Fallback: if Gemini is slow/fails, return raw yfinance data anyway
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.core.config import get_settings
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


def _fmt_pct(val: Any) -> str:
    if val is None:
        return "N/A"
    try:
        return f"{float(val) * 100:.1f}%"
    except (ValueError, TypeError):
        return "N/A"


def _get_gemini_key() -> str:
    key = get_settings().gemini_api_key
    if not key:
        key = os.getenv("GEMINI_API_KEY", "")
    return key


def _segments_to_pct(segments: dict[str, float]) -> dict[str, str]:
    """Convert absolute segment values to percentage strings."""
    total = sum(abs(v) for v in segments.values() if v > 0)
    if total <= 0:
        return {}
    return {k: f"{v / total * 100:.1f}%" for k, v in segments.items() if v > 0}


# ── Data Aggregator (yfinance only) ─────────────────────────────

async def _aggregate_data(symbol: str) -> dict[str, Any]:
    """
    Fetch all data from yfinance via a single Ticker object.
    No FMP calls — avoids 403 errors entirely.
    """
    raw = await get_strategic_data(symbol)

    market_cap = _safe_float(raw.get("market_cap"))

    return {
        "symbol": symbol,
        "company_name": raw.get("company_name"),
        "business_description": raw.get("business_description"),
        "sector": raw.get("sector"),
        "industry": raw.get("industry"),
        "country": raw.get("country"),
        "currency": raw.get("currency"),
        "market_cap": market_cap,
        "market_cap_display": _fmt_large(market_cap),
        "current_price": raw.get("current_price"),
        "trailing_pe": raw.get("trailing_pe"),
        "profit_margins": raw.get("profit_margins"),
        "revenue_growth": raw.get("revenue_growth"),
        "earnings_growth": raw.get("earnings_growth"),
        "return_on_equity": raw.get("return_on_equity"),
        "total_revenue": raw.get("total_revenue"),
        "full_time_employees": raw.get("full_time_employees"),
        "revenue_mix": raw.get("revenue_mix", {}),
    }


# ── LLM System Prompt ───────────────────────────────────────────

SYSTEM_PROMPT = """\
Tu es un analyste senior en Private Equity. Tu reçois un dossier de données brutes sur une entreprise cotée.

**Tes tâches — en français :**

1. **Identité Flash** : Synthétise l'entreprise en exactement 3 phrases courtes et percutantes. \
Qui est-elle ? Que fait-elle ? Quelle est sa position de marché ?

2. **Moat Score** : Note l'avantage compétitif sur 4 piliers (score de 1 à 5 chacun). \
Pour chaque pilier, donne un commentaire d'une phrase.
   - **Pricing Power** : capacité à augmenter les prix sans perdre de clients
   - **Switching Cost** : coût pour un client de changer de fournisseur
   - **Network Effect** : valeur qui augmente avec le nombre d'utilisateurs
   - **Intangible Assets** : marques, brevets, licences réglementaires

3. **SWOT Percutant** : Génère exactement 2 Forces [+] et 2 Faiblesses [-]. \
Chaque item = un label court (3-5 mots) + un détail explicatif (1 phrase).

**Contraintes strictes :**
- INTERDICTION d'halluciner. Si une donnée est absente ou insuffisante, écris "Données limitées" pour ce champ.
- Base-toi UNIQUEMENT sur les données fournies ci-dessous.
- Réponds UNIQUEMENT en JSON valide, sans texte autour, avec cette structure exacte :

```json
{
  "identity_flash": "string (3 phrases)",
  "moat_pillars": [
    {"name": "Pricing Power", "score": 1-5, "comment": "string"},
    {"name": "Switching Cost", "score": 1-5, "comment": "string"},
    {"name": "Network Effect", "score": 1-5, "comment": "string"},
    {"name": "Intangible Assets", "score": 1-5, "comment": "string"}
  ],
  "swot": {
    "strengths": [
      {"label": "string", "detail": "string"},
      {"label": "string", "detail": "string"}
    ],
    "weaknesses": [
      {"label": "string", "detail": "string"},
      {"label": "string", "detail": "string"}
    ]
  }
}
```"""


def _build_user_prompt(data: dict[str, Any]) -> str:
    """Build a single comprehensive prompt with all yfinance data."""
    revenue_pct = _segments_to_pct(data.get("revenue_mix", {}))

    sections = [
        f"## Entreprise : {data.get('company_name', 'N/A')} ({data['symbol']})",
        f"Secteur : {data.get('sector', 'N/A')} | Industrie : {data.get('industry', 'N/A')}",
        f"Pays : {data.get('country', 'N/A')} | Devise : {data.get('currency', 'N/A')}",
        f"Market Cap : {data.get('market_cap_display', 'N/A')}",
        f"P/E : {data.get('trailing_pe', 'N/A')} | Marge nette : {_fmt_pct(data.get('profit_margins'))}",
        f"Croissance CA : {_fmt_pct(data.get('revenue_growth'))} | Croissance BPA : {_fmt_pct(data.get('earnings_growth'))}",
        f"ROE : {_fmt_pct(data.get('return_on_equity'))}",
        f"CA total : {_fmt_large(_safe_float(data.get('total_revenue')))}",
        f"Employés : {data.get('full_time_employees', 'N/A')}",
        "",
        "## Description Business",
        data.get("business_description") or "Données non disponibles.",
        "",
        "## Revenue Mix (structure du P&L)",
        json.dumps(revenue_pct, ensure_ascii=False, indent=2) if revenue_pct else "Données non disponibles.",
    ]

    return "\n".join(sections)


# ── LLM Call (Gemini) ────────────────────────────────────────────

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"

# Timeout for Gemini: 25s request + 5s safety margin
_GEMINI_TIMEOUT = 25.0


async def _call_llm(user_prompt: str) -> Optional[dict]:
    """Call Gemini. Returns parsed JSON or None on any failure."""
    key = _get_gemini_key()
    if not key:
        logger.error("GEMINI_API_KEY is empty — set it in Railway env vars or .env file")
        return None

    key_preview = key[:8] + "..." if len(key) > 8 else "***"
    logger.info("Gemini call: key=%s, prompt_len=%d", key_preview, len(user_prompt))

    payload = {
        "systemInstruction": {
            "parts": [{"text": SYSTEM_PROMPT}],
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": user_prompt}],
            }
        ],
        "generationConfig": {
            "responseMimeType": "application/json",
            "maxOutputTokens": 1024,
            "temperature": 0.3,
        },
    }

    try:
        async with httpx.AsyncClient(timeout=_GEMINI_TIMEOUT) as client:
            resp = await client.post(
                GEMINI_URL,
                json=payload,
                params={"key": key},
                headers={"content-type": "application/json"},
            )

        if resp.status_code == 429:
            logger.warning("Gemini 429 rate limit — returning fallback data")
            return None

        if resp.status_code != 200:
            logger.error("Gemini API error: status=%d body=%s", resp.status_code, resp.text[:500])
            return None

        body = resp.json()

        candidates = body.get("candidates", [])
        if not candidates:
            logger.error("Gemini returned no candidates: %s", json.dumps(body)[:300])
            return None

        text = candidates[0]["content"]["parts"][0]["text"]

        # Strip markdown code fences if present (safety net)
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        logger.info("Gemini response parsed OK (%d chars)", len(text))
        return json.loads(text)

    except httpx.TimeoutException:
        logger.warning("Gemini timeout after %.0fs — returning fallback data", _GEMINI_TIMEOUT)
        return None
    except json.JSONDecodeError as exc:
        logger.error("Gemini returned invalid JSON: %s", exc)
        return None
    except Exception as exc:
        logger.error("Gemini call failed: %s", exc)
        return None


# ── Main Entry Point ─────────────────────────────────────────────

async def run_strategic_analysis(symbol: str) -> StrategicAnalysis:
    """
    Execute the full strategic analysis pipeline.
    ALWAYS returns a complete JSON object — uses raw yfinance data as
    fallback if Gemini is slow, fails, or the API key is missing.
    """
    logger.info("=== STRATEGIC ANALYSIS START for %s ===", symbol)

    # Step 1: Aggregate data (yfinance only — no FMP)
    data = await _aggregate_data(symbol)
    flags: list[str] = []

    if not data.get("business_description"):
        flags.append("DESCRIPTION_UNAVAILABLE")

    revenue_mix = data.get("revenue_mix", {})

    # Step 2: Build single prompt & call Gemini
    user_prompt = _build_user_prompt(data)
    llm_result = await _call_llm(user_prompt)

    # Step 3: Parse LLM output OR build fallback from raw data
    identity_flash: Optional[str] = None
    moat_pillars: list[MoatPillar] = []
    moat_average: Optional[float] = None
    swot = SWOT()

    if llm_result:
        identity_flash = llm_result.get("identity_flash")

        raw_pillars = llm_result.get("moat_pillars", [])
        for p in raw_pillars:
            score = p.get("score", 0)
            if isinstance(score, (int, float)) and 1 <= score <= 5:
                moat_pillars.append(MoatPillar(
                    name=p.get("name", "N/A"),
                    score=int(score),
                    comment=p.get("comment", "Données limitées"),
                ))

        if moat_pillars:
            moat_average = round(sum(p.score for p in moat_pillars) / len(moat_pillars), 2)

        raw_swot = llm_result.get("swot", {})
        strengths = [
            SWOTItem(label=s.get("label", "N/A"), detail=s.get("detail", "Données limitées"))
            for s in raw_swot.get("strengths", [])[:2]
        ]
        weaknesses = [
            SWOTItem(label=w.get("label", "N/A"), detail=w.get("detail", "Données limitées"))
            for w in raw_swot.get("weaknesses", [])[:2]
        ]
        swot = SWOT(strengths=strengths, weaknesses=weaknesses)
    else:
        # Fallback: map longBusinessSummary as identity_flash
        desc = data.get("business_description")
        if desc:
            # Take first 3 sentences as identity flash
            sentences = [s.strip() for s in desc.replace("\n", " ").split(".") if s.strip()]
            identity_flash = ". ".join(sentences[:3]) + "." if sentences else None

        flags.append("LLM_UNAVAILABLE")
        logger.warning("Gemini unavailable for %s — returning raw yfinance fallback", symbol)

    result = StrategicAnalysis(
        symbol=symbol,
        company_name=data.get("company_name"),
        business_description=data.get("business_description"),
        sector=data.get("sector"),
        industry=data.get("industry"),
        country=data.get("country"),
        market_cap_display=data.get("market_cap_display", "N/A"),
        product_segments=revenue_mix,
        geo_segments={},
        identity_flash=identity_flash,
        moat_pillars=moat_pillars,
        moat_average=moat_average,
        swot=swot,
        flags=flags,
        source="yfinance+gemini" if llm_result else "yfinance",
        cached_at=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        "=== STRATEGIC ANALYSIS END for %s: moat_avg=%s flags=%s source=%s ===",
        symbol, moat_average, flags, result.source,
    )
    return result
