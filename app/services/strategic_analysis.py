"""
Strategic Analysis Engine — Data Aggregator + LLM Synthesis.

Pipeline:
  1. Aggregate data: FMP profile, product/geo segmentation, yfinance market data
  2. Build structured prompt for Claude (PE analyst persona)
  3. Parse structured JSON response → StrategicAnalysis
"""

import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from app.core.config import get_settings
from app.core.logging import logger
from app.integration.fmp_client import (
    get_company_profile,
    get_revenue_segmentation,
    get_revenue_geo_segmentation,
)
from app.integration import yfinance_client
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


def _get_anthropic_key() -> str:
    key = get_settings().anthropic_api_key
    if not key:
        key = os.getenv("ANTHROPIC_API_KEY", "")
    return key


def _segments_to_pct(segments: dict[str, float]) -> dict[str, str]:
    """Convert absolute segment values to percentage strings."""
    total = sum(segments.values())
    if total <= 0:
        return {}
    return {k: f"{v / total * 100:.1f}%" for k, v in segments.items()}


# ── Data Aggregator ──────────────────────────────────────────────

async def _aggregate_data(symbol: str) -> dict[str, Any]:
    """
    Fetch all data sources in parallel and merge into a single context dict.
    Reuses the yfinance info call for market data (market cap, price, etc.).
    """
    import asyncio

    profile_task = get_company_profile(symbol)
    product_seg_task = get_revenue_segmentation(symbol)
    geo_seg_task = get_revenue_geo_segmentation(symbol)
    info_task = yfinance_client.get_ticker_info(symbol)

    profile, product_seg, geo_seg, info = await asyncio.gather(
        profile_task, product_seg_task, geo_seg_task, info_task,
        return_exceptions=True,
    )

    # Gracefully handle exceptions from individual fetchers
    if isinstance(profile, Exception):
        logger.warning("Profile fetch failed for %s: %s", symbol, profile)
        profile = None
    if isinstance(product_seg, Exception):
        logger.warning("Product seg fetch failed for %s: %s", symbol, product_seg)
        product_seg = None
    if isinstance(geo_seg, Exception):
        logger.warning("Geo seg fetch failed for %s: %s", symbol, geo_seg)
        geo_seg = None
    if isinstance(info, Exception):
        logger.warning("yfinance info fetch failed for %s: %s", symbol, info)
        info = {}

    market_cap = _safe_float(info.get("marketCap")) if isinstance(info, dict) else None

    return {
        "symbol": symbol,
        "company_name": (profile or {}).get("companyName") or info.get("longName"),
        "business_description": (profile or {}).get("description"),
        "sector": (profile or {}).get("sector") or info.get("sector"),
        "industry": (profile or {}).get("industry") or info.get("industry"),
        "country": (profile or {}).get("country"),
        "market_cap": market_cap,
        "market_cap_display": _fmt_large(market_cap),
        "current_price": info.get("currentPrice") or info.get("regularMarketPrice"),
        "currency": info.get("currency"),
        "product_segments": product_seg or {},
        "geo_segments": geo_seg or {},
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
    """Build the user message with all aggregated data."""
    product_pct = _segments_to_pct(data.get("product_segments", {}))
    geo_pct = _segments_to_pct(data.get("geo_segments", {}))

    sections = [
        f"## Entreprise : {data.get('company_name', 'N/A')} ({data['symbol']})",
        f"Secteur : {data.get('sector', 'N/A')} | Industrie : {data.get('industry', 'N/A')}",
        f"Pays : {data.get('country', 'N/A')} | Devise : {data.get('currency', 'N/A')}",
        f"Market Cap : {data.get('market_cap_display', 'N/A')}",
        "",
        "## Description Business",
        data.get("business_description") or "Données non disponibles.",
        "",
        "## Segmentation Produit (% du CA)",
        json.dumps(product_pct, ensure_ascii=False, indent=2) if product_pct else "Données non disponibles.",
        "",
        "## Segmentation Géographique (% du CA)",
        json.dumps(geo_pct, ensure_ascii=False, indent=2) if geo_pct else "Données non disponibles.",
    ]

    return "\n".join(sections)


# ── LLM Call ─────────────────────────────────────────────────────

async def _call_llm(user_prompt: str) -> Optional[dict]:
    """Call Claude via Anthropic Messages API. Returns parsed JSON or None."""
    key = _get_anthropic_key()
    if not key:
        logger.error("ANTHROPIC_API_KEY is empty — set it in Railway env vars or .env file")
        return None

    payload = {
        "model": "claude-sonnet-4-20250514",
        "max_tokens": 1024,
        "system": SYSTEM_PROMPT,
        "messages": [{"role": "user", "content": user_prompt}],
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                json=payload,
                headers={
                    "x-api-key": key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )

        if resp.status_code != 200:
            logger.error("Anthropic API error: status=%d body=%s", resp.status_code, resp.text[:500])
            return None

        body = resp.json()
        text = body["content"][0]["text"]

        # Strip markdown code fences if present
        text = text.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        return json.loads(text)

    except json.JSONDecodeError as exc:
        logger.error("LLM returned invalid JSON: %s", exc)
        return None
    except Exception as exc:
        logger.error("LLM call failed: %s", exc)
        return None


# ── Main Entry Point ─────────────────────────────────────────────

async def run_strategic_analysis(symbol: str) -> StrategicAnalysis:
    """Execute the full strategic analysis pipeline."""
    logger.info("=== STRATEGIC ANALYSIS START for %s ===", symbol)

    # Step 1: Aggregate data
    data = await _aggregate_data(symbol)
    flags: list[str] = []

    if not data.get("business_description"):
        flags.append("DESCRIPTION_UNAVAILABLE")
    if not data.get("product_segments"):
        flags.append("PRODUCT_SEGMENTS_UNAVAILABLE")
    if not data.get("geo_segments"):
        flags.append("GEO_SEGMENTS_UNAVAILABLE")

    # Step 2: Build prompt & call LLM
    user_prompt = _build_user_prompt(data)
    llm_result = await _call_llm(user_prompt)

    # Step 3: Parse LLM output
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
        flags.append("LLM_UNAVAILABLE")

    result = StrategicAnalysis(
        symbol=symbol,
        company_name=data.get("company_name"),
        business_description=data.get("business_description"),
        sector=data.get("sector"),
        industry=data.get("industry"),
        country=data.get("country"),
        market_cap_display=data.get("market_cap_display", "N/A"),
        product_segments=data.get("product_segments", {}),
        geo_segments=data.get("geo_segments", {}),
        identity_flash=identity_flash,
        moat_pillars=moat_pillars,
        moat_average=moat_average,
        swot=swot,
        flags=flags,
        cached_at=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        "=== STRATEGIC ANALYSIS END for %s: moat_avg=%s flags=%s ===",
        symbol, moat_average, flags,
    )
    return result
