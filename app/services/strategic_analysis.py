"""
Strategic Analysis Engine — yfinance + Musaffa/FMP segments only.

No AI, no Gemini, no LLM. Pure data mapping.

Pipeline:
  1. yf.Ticker(symbol).info  → identity, pitch, sector, market_cap
  2. FMP/Musaffa segments    → revenue_mix (product breakdown)
  3. Sector-based defaults   → moat pillars + SWOT
  4. Always returns a valid StrategicAnalysis (never raises)
"""

from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logging import logger
from app.integration.yfinance_client import get_ticker_info
from app.integration.fmp_client import get_revenue_segmentation as fmp_revenue_segmentation
from app.integration.musaffa_scraper import scrape_revenue_breakdown
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


# ── Sector-based Moat & SWOT defaults ────────────────────────────

_SECTOR_MOAT: dict[str, list[dict[str, Any]]] = {
    "Technology": [
        {"name": "Pricing Power",    "score": 4, "comment": "Forte capacité à imposer des prix premium grâce à l'innovation."},
        {"name": "Switching Cost",   "score": 4, "comment": "Écosystème logiciel intégré créant une forte rétention."},
        {"name": "Network Effect",   "score": 3, "comment": "Effet réseau modéré via les plateformes et APIs."},
        {"name": "Intangible Assets","score": 4, "comment": "Brevets, marques et propriété intellectuelle significatifs."},
    ],
    "Healthcare": [
        {"name": "Pricing Power",    "score": 3, "comment": "Pouvoir de prix soutenu par les brevets et l'exclusivité réglementaire."},
        {"name": "Switching Cost",   "score": 3, "comment": "Coût de changement modéré lié aux protocoles médicaux."},
        {"name": "Network Effect",   "score": 2, "comment": "Effet réseau limité dans le secteur santé."},
        {"name": "Intangible Assets","score": 5, "comment": "Brevets pharma, autorisations FDA, données cliniques."},
    ],
    "Financial Services": [
        {"name": "Pricing Power",    "score": 3, "comment": "Marges dépendantes des taux d'intérêt et de la concurrence."},
        {"name": "Switching Cost",   "score": 4, "comment": "Forte inertie des clients bancaires et institutionnels."},
        {"name": "Network Effect",   "score": 3, "comment": "Effet réseau via les systèmes de paiement."},
        {"name": "Intangible Assets","score": 3, "comment": "Licences bancaires et confiance de marque."},
    ],
    "Consumer Cyclical": [
        {"name": "Pricing Power",    "score": 3, "comment": "Pouvoir de prix variable selon la force de la marque."},
        {"name": "Switching Cost",   "score": 2, "comment": "Faible coût de changement pour le consommateur."},
        {"name": "Network Effect",   "score": 2, "comment": "Effet réseau limité hors plateformes e-commerce."},
        {"name": "Intangible Assets","score": 4, "comment": "Valeur de marque et fidélité client."},
    ],
    "Industrials": [
        {"name": "Pricing Power",    "score": 3, "comment": "Pouvoir de prix modéré, soumis aux cycles économiques."},
        {"name": "Switching Cost",   "score": 3, "comment": "Contrats long terme et intégration opérationnelle."},
        {"name": "Network Effect",   "score": 1, "comment": "Effet réseau quasi inexistant dans l'industrie."},
        {"name": "Intangible Assets","score": 3, "comment": "Brevets industriels et certifications."},
    ],
    "Energy": [
        {"name": "Pricing Power",    "score": 2, "comment": "Prix largement dictés par les marchés mondiaux."},
        {"name": "Switching Cost",   "score": 2, "comment": "Faible coût de changement entre fournisseurs."},
        {"name": "Network Effect",   "score": 1, "comment": "Pas d'effet réseau significatif."},
        {"name": "Intangible Assets","score": 3, "comment": "Concessions, droits d'exploitation et expertise technique."},
    ],
    "Consumer Defensive": [
        {"name": "Pricing Power",    "score": 3, "comment": "Pouvoir de prix stable grâce aux marques de confiance."},
        {"name": "Switching Cost",   "score": 2, "comment": "Faible barrière au changement pour les biens courants."},
        {"name": "Network Effect",   "score": 1, "comment": "Effet réseau négligeable."},
        {"name": "Intangible Assets","score": 4, "comment": "Marques historiques et réseaux de distribution."},
    ],
    "Communication Services": [
        {"name": "Pricing Power",    "score": 3, "comment": "Modèles d'abonnement et contenus exclusifs."},
        {"name": "Switching Cost",   "score": 3, "comment": "Habitudes utilisateur et bibliothèques de contenu."},
        {"name": "Network Effect",   "score": 5, "comment": "Fort effet réseau sur les plateformes sociales et médias."},
        {"name": "Intangible Assets","score": 4, "comment": "Licences, catalogues de contenu, données utilisateurs."},
    ],
    "Real Estate": [
        {"name": "Pricing Power",    "score": 3, "comment": "Pouvoir de prix lié à l'emplacement et la rareté."},
        {"name": "Switching Cost",   "score": 4, "comment": "Baux long terme et coûts de déménagement élevés."},
        {"name": "Network Effect",   "score": 1, "comment": "Pas d'effet réseau notable."},
        {"name": "Intangible Assets","score": 3, "comment": "Portefeuille immobilier et emplacements stratégiques."},
    ],
    "Basic Materials": [
        {"name": "Pricing Power",    "score": 2, "comment": "Prix dictés par les marchés des matières premières."},
        {"name": "Switching Cost",   "score": 2, "comment": "Faible différenciation entre fournisseurs."},
        {"name": "Network Effect",   "score": 1, "comment": "Aucun effet réseau."},
        {"name": "Intangible Assets","score": 2, "comment": "Concessions minières et droits d'exploitation."},
    ],
    "Utilities": [
        {"name": "Pricing Power",    "score": 2, "comment": "Prix régulés par les autorités publiques."},
        {"name": "Switching Cost",   "score": 5, "comment": "Monopole naturel, changement quasi impossible."},
        {"name": "Network Effect",   "score": 1, "comment": "Pas d'effet réseau."},
        {"name": "Intangible Assets","score": 3, "comment": "Licences d'exploitation et infrastructure réglementée."},
    ],
}

_DEFAULT_MOAT = [
    {"name": "Pricing Power",    "score": 0, "comment": "Données techniques en cours de calcul"},
    {"name": "Switching Cost",   "score": 0, "comment": "Données techniques en cours de calcul"},
    {"name": "Network Effect",   "score": 0, "comment": "Données techniques en cours de calcul"},
    {"name": "Intangible Assets","score": 0, "comment": "Données techniques en cours de calcul"},
]

_SECTOR_SWOT: dict[str, dict[str, list[dict[str, str]]]] = {
    "Technology": {
        "strengths": [
            {"label": "Innovation", "detail": "Investissements R&D élevés et pipeline de produits actif."},
            {"label": "Marges élevées", "detail": "Modèle scalable avec des marges brutes généralement supérieures à 50%."},
        ],
        "weaknesses": [
            {"label": "Concurrence intense", "detail": "Secteur en disruption permanente avec des cycles courts."},
            {"label": "Dépendance réglementaire", "detail": "Exposition aux régulations antitrust et données personnelles."},
        ],
    },
    "Healthcare": {
        "strengths": [
            {"label": "Demande résiliente", "detail": "Secteur défensif avec une demande structurelle croissante."},
            {"label": "Barrières à l'entrée", "detail": "Autorisations réglementaires et brevets protègent les positions."},
        ],
        "weaknesses": [
            {"label": "Risque réglementaire", "detail": "Politiques de prix des médicaments et réformes de santé."},
            {"label": "Dépendance au pipeline", "detail": "Résultats liés au succès des essais cliniques."},
        ],
    },
    "Financial Services": {
        "strengths": [
            {"label": "Revenus récurrents", "detail": "Flux stables via les frais de gestion et intérêts."},
            {"label": "Effet d'échelle", "detail": "Avantage compétitif des grandes institutions financières."},
        ],
        "weaknesses": [
            {"label": "Sensibilité aux taux", "detail": "Performance fortement corrélée aux politiques monétaires."},
            {"label": "Risque systémique", "detail": "Exposition aux crises financières et au risque de crédit."},
        ],
    },
}

_DEFAULT_SWOT = {
    "strengths": [
        {"label": "En attente", "detail": "Données techniques en cours de calcul"},
        {"label": "En attente", "detail": "Données techniques en cours de calcul"},
    ],
    "weaknesses": [
        {"label": "En attente", "detail": "Données techniques en cours de calcul"},
        {"label": "En attente", "detail": "Données techniques en cours de calcul"},
    ],
}


def _build_moat(sector: Optional[str]) -> tuple[list[MoatPillar], Optional[float]]:
    """Return sector-based moat pillars + average score."""
    raw = _SECTOR_MOAT.get(sector or "", _DEFAULT_MOAT)
    pillars = [MoatPillar(**p) for p in raw]
    scores = [p.score for p in pillars if p.score > 0]
    avg = round(sum(scores) / len(scores), 1) if scores else None
    return pillars, avg


def _build_swot(sector: Optional[str]) -> SWOT:
    """Return sector-based SWOT."""
    raw = _SECTOR_SWOT.get(sector or "", _DEFAULT_SWOT)
    return SWOT(
        strengths=[SWOTItem(**s) for s in raw["strengths"]],
        weaknesses=[SWOTItem(**w) for w in raw["weaknesses"]],
    )


# ── Revenue Mix: FMP → Musaffa fallback ──────────────────────────

async def _fetch_revenue_mix(symbol: str) -> tuple[dict[str, float], str]:
    """
    Try FMP revenue segmentation, then Musaffa scraper.
    Returns (segments_dict, source).
    """
    # Attempt 1: FMP
    try:
        fmp_seg = await fmp_revenue_segmentation(symbol)
        if fmp_seg:
            segments = {}
            for name, val in fmp_seg.items():
                v = _safe_float(val)
                if v is not None and v > 0:
                    segments[name] = v
            if segments:
                logger.info("Revenue mix source: FMP for %s (%d segments)", symbol, len(segments))
                return segments, "FMP"
    except Exception as exc:
        logger.debug("FMP revenue segmentation failed for %s: %s", symbol, exc)

    # Attempt 2: Musaffa
    try:
        musaffa = await scrape_revenue_breakdown(symbol)
        if musaffa and musaffa.get("segments"):
            segments = {k: v for k, v in musaffa["segments"].items() if v > 0}
            if segments:
                logger.info("Revenue mix source: Musaffa for %s (%d segments)", symbol, len(segments))
                return segments, "musaffa"
    except Exception as exc:
        logger.debug("Musaffa revenue scraping failed for %s: %s", symbol, exc)

    logger.info("No revenue segments found for %s", symbol)
    return {}, "N/A"


# ── Main Entry Point ─────────────────────────────────────────────

async def run_strategic_analysis(symbol: str) -> StrategicAnalysis:
    """
    Source unique: yf.Ticker(symbol).info + FMP/Musaffa segments.
    Always returns a valid StrategicAnalysis — never raises.
    """
    logger.info("=== STRATEGIC ANALYSIS START for %s ===", symbol)

    flags: list[str] = []

    # ── 1. yfinance info ──────────────────────────────────────────
    try:
        info = await get_ticker_info(symbol)
    except Exception as exc:
        logger.error("yfinance info failed for %s: %s", symbol, exc)
        flags.append("YFINANCE_UNAVAILABLE")
        return StrategicAnalysis(
            symbol=symbol,
            flags=flags,
            source="yfinance",
            cached_at=datetime.now(timezone.utc).isoformat(),
        )

    company_name = info.get("longName") or info.get("shortName")
    sector = info.get("sector")
    industry = info.get("industry")
    country = info.get("country")
    market_cap = _safe_float(info.get("marketCap"))

    # ── 2. Pitch = longBusinessSummary (intégral) ─────────────────
    description = info.get("longBusinessSummary")
    identity_flash = description  # Full text, not truncated
    if not description:
        flags.append("DESCRIPTION_UNAVAILABLE")

    # ── 3. Revenue Mix: FMP → Musaffa ─────────────────────────────
    revenue_mix, seg_source = await _fetch_revenue_mix(symbol)
    if not revenue_mix:
        flags.append("SEGMENTATION_UNAVAILABLE")

    # ── 4. Moat & SWOT: basés sur le secteur ──────────────────────
    moat_pillars, moat_average = _build_moat(sector)
    swot = _build_swot(sector)

    result = StrategicAnalysis(
        symbol=symbol,
        company_name=company_name,
        business_description=description,
        sector=sector,
        industry=industry,
        country=country,
        market_cap_display=_fmt_large(market_cap),
        product_segments=revenue_mix,
        geo_segments={},
        identity_flash=identity_flash,
        moat_pillars=moat_pillars,
        moat_average=moat_average,
        swot=swot,
        flags=flags,
        source=f"yfinance+{seg_source}" if seg_source != "N/A" else "yfinance",
        cached_at=datetime.now(timezone.utc).isoformat(),
    )

    logger.info(
        "=== STRATEGIC ANALYSIS END for %s: pitch=%d chars, segments=%d (%s), sector=%s, moat_avg=%s ===",
        symbol, len(identity_flash or ""), len(revenue_mix), seg_source, sector, moat_average,
    )
    return result
