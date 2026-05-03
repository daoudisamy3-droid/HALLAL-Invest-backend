"""
Macro Engine — global market data, enriched FRED series, market regime, economic calendar.

Reuses fetch_fred_macro() from market_context.py (no duplication).
Adds: global market tickers, extra FRED series, regime scoring, calendar.

Cache TTL:
  global_markets  — 15 min (quasi real-time)
  fred_enriched   — 1 h
  full dashboard  — 15 min
"""

import asyncio
import os
from datetime import date

import httpx
import yfinance as yf

from app.core.cache import cache_get, cache_set
from app.core.logging import logger

_NS = "macro_dashboard"

TICKERS_GLOBAUX = {
    "sp500":  {"ticker": "^GSPC",    "label": "S&P 500",      "type": "index"},
    "nasdaq": {"ticker": "^IXIC",    "label": "NASDAQ",       "type": "index"},
    "vix":    {"ticker": "^VIX",     "label": "VIX",          "type": "fear"},
    "oil":    {"ticker": "CL=F",     "label": "Pétrole WTI",  "type": "commodity"},
    "gold":   {"ticker": "GC=F",     "label": "Or",           "type": "commodity"},
    "copper": {"ticker": "HG=F",     "label": "Cuivre",       "type": "commodity"},
    "eurusd": {"ticker": "EURUSD=X", "label": "EUR/USD",      "type": "fx"},
    "dxy":    {"ticker": "DX-Y.NYB", "label": "Dollar DXY",   "type": "fx"},
    "tnx":    {"ticker": "^TNX",     "label": "10Y Treasury", "type": "rates"},
    "tyx":    {"ticker": "^TYX",     "label": "30Y Treasury", "type": "rates"},
}

FRED_EXTRA_SERIES = {
    "yield_curve":  "T10Y2Y",    # Spread 10Y-2Y (récession si < 0)
    "treasury_10y": "DGS10",     # Taux 10Y
    "treasury_2y":  "DGS2Y",     # Taux 2Y
    "pce":          "PCEPI",     # PCE inflation (cible Fed)
    "retail_sales": "RSAFS",     # Ventes au détail
    "ism_mfg":      "MANEMP",    # Emploi manufacturier proxy
}

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


# ── Global markets ────────────────────────────────────────────────

def _fetch_all_markets_blocking() -> dict:
    results: dict = {}
    for key, cfg in TICKERS_GLOBAUX.items():
        try:
            t = yf.Ticker(cfg["ticker"])
            info = t.fast_info
            price = float(info.get("last_price") or 0)
            prev = float(info.get("previous_close") or price)
            change_pct = (price - prev) / prev * 100 if prev else 0.0

            hist = t.history(period="3mo")
            perf_1m = perf_3m = None
            if not hist.empty:
                p_now = float(hist["Close"].iloc[-1])
                if len(hist) >= 21:
                    perf_1m = round(
                        (p_now - float(hist["Close"].iloc[-21]))
                        / float(hist["Close"].iloc[-21]) * 100, 2
                    )
                if len(hist) >= 63:
                    perf_3m = round(
                        (p_now - float(hist["Close"].iloc[0]))
                        / float(hist["Close"].iloc[0]) * 100, 2
                    )

            results[key] = {
                "label": cfg["label"],
                "type": cfg["type"],
                "price": round(price, 4),
                "change_pct": round(change_pct, 2),
                "perf_1m": perf_1m,
                "perf_3m": perf_3m,
                "trend": "hausse" if change_pct > 0 else "baisse",
            }
        except Exception as exc:
            logger.warning("macro_engine/global_markets/%s: %s", key, exc)
            results[key] = None
    return results


async def fetch_global_markets() -> dict:
    """Fetch all global market tickers via YFinance. Cache 15 min."""
    cached = cache_get(_NS, "global_markets")
    if cached:
        return cached

    loop = asyncio.get_running_loop()
    results = await loop.run_in_executor(None, _fetch_all_markets_blocking)

    cache_set(_NS, "global_markets", results, ttl=900)
    logger.info("macro_engine: global_markets fetched (%d tickers)", len(results))
    return results


# ── FRED enriched ─────────────────────────────────────────────────

async def fetch_fred_enriched() -> dict:
    """
    Fetch additional FRED series not covered by fetch_fred_macro().
    Returns empty dict if FRED_API_KEY is absent. Cache 1h.
    """
    cached = cache_get(_NS, "fred_enriched")
    if cached:
        return cached

    fred_key = os.getenv("FRED_API_KEY", "")
    if not fred_key:
        return {}

    result: dict = {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        for key, series_id in FRED_EXTRA_SERIES.items():
            try:
                r = await client.get(
                    _FRED_BASE,
                    params={
                        "series_id": series_id,
                        "api_key": fred_key,
                        "file_type": "json",
                        "limit": 3,
                        "sort_order": "desc",
                    },
                )
                if r.status_code == 200:
                    obs = r.json().get("observations", [])
                    if obs:
                        val = obs[0].get("value", ".")
                        prev = obs[1].get("value", ".") if len(obs) > 1 else "."
                        val_f = float(val) if val != "." else None
                        prev_f = float(prev) if prev != "." else None
                        result[key] = {
                            "value": val_f,
                            "previous": prev_f,
                            "date": obs[0].get("date"),
                            "trend": (
                                "hausse"
                                if val_f is not None and prev_f is not None and val_f > prev_f
                                else "baisse"
                            ),
                        }
            except Exception as exc:
                logger.warning("macro_engine/fred_enriched/%s: %s", key, exc)

    cache_set(_NS, "fred_enriched", result, ttl=3600)
    return result


# ── Market regime ─────────────────────────────────────────────────

def compute_market_regime(fred_base: dict, fred_extra: dict, markets: dict) -> dict:
    """
    Scores the global market regime from all data sources.
    Returns RISK-ON / NEUTRE / RISK-OFF with score 0-100.
    """
    score = 50
    factors: list[str] = []
    warnings: list[str] = []

    # VIX
    vix = (markets.get("vix") or {})
    v = vix.get("price")
    if v:
        if v < 15:
            score += 15
            factors.append(f"VIX {v:.1f} — marché serein (< 15)")
        elif v < 20:
            score += 8
            factors.append(f"VIX {v:.1f} — volatilité normale")
        elif v < 25:
            score -= 5
            warnings.append(f"VIX {v:.1f} — tension modérée")
        elif v < 30:
            score -= 15
            warnings.append(f"VIX {v:.1f} — stress marché")
        else:
            score -= 25
            warnings.append(f"VIX {v:.1f} — PANIQUE MARCHÉ")

    # Yield curve 10Y-2Y
    yc_data = fred_extra.get("yield_curve") or {}
    yc = yc_data.get("value")
    if yc is not None:
        if yc > 0.5:
            score += 10
            factors.append(f"Courbe taux normale (+{yc:.2f}%)")
        elif yc > 0:
            score += 3
            factors.append(f"Courbe taux légèrement plate ({yc:.2f}%)")
        elif yc > -0.5:
            score -= 10
            warnings.append(f"Courbe inversée ({yc:.2f}%) — signal récession")
        else:
            score -= 20
            warnings.append(f"Courbe fortement inversée ({yc:.2f}%) — récession probable")

    # Fed rate
    fed = fred_base.get("fed_rate") or {}
    rate = fed.get("value")
    if rate is not None:
        trend = fed.get("trend")
        if trend == "baisse":
            score += 15
            factors.append(f"Fed en mode pivot ({rate:.2f}% ↓)")
        elif trend == "hausse":
            score -= 10
            warnings.append(f"Fed hawkish ({rate:.2f}% ↑)")
        if rate < 3.0:
            score += 10
        elif rate > 5.0:
            score -= 10

    # HY credit spread
    credit = fred_base.get("credit_spread") or {}
    spread = credit.get("value")
    if spread is not None:
        if spread < 3.0:
            score += 10
            factors.append(f"Spreads crédit serrés ({spread:.2f}%) — appétit risque")
        elif spread > 7.0:
            score -= 20
            warnings.append(f"Spreads crédit larges ({spread:.2f}%) — fuite risque")

    # S&P 500 1-month trend
    sp500 = (markets.get("sp500") or {})
    p1m = sp500.get("perf_1m")
    if p1m is not None:
        if p1m > 3:
            score += 10
            factors.append(f"S&P 500 +{p1m:.1f}% sur 1 mois")
        elif p1m < -5:
            score -= 15
            warnings.append(f"S&P 500 {p1m:.1f}% sur 1 mois — correction")

    score = max(0, min(100, score))

    if score >= 65:
        regime, color = "RISK-ON", "green"
        note = "Conditions favorables aux actions. Momentum positif."
    elif score >= 40:
        regime, color = "NEUTRE", "amber"
        note = "Marché indécis. Sélectivité recommandée."
    else:
        regime, color = "RISK-OFF", "red"
        note = "Conditions défavorables. Prudence et défensif."

    return {
        "regime": regime,
        "regime_color": color,
        "regime_note": note,
        "score": score,
        "factors": factors,
        "warnings": warnings,
    }


# ── Economic calendar ─────────────────────────────────────────────

def build_economic_calendar() -> list:
    """Upcoming major macro events. Updated manually each quarter."""
    today = date.today()

    events = [
        {
            "date": "2026-05-07",
            "event": "Décision Fed (FOMC)",
            "importance": "HIGH",
            "impact": "Taux directeurs — impact direct sur toutes les actions",
            "consensus": "Hold attendu à 3.50-3.75%",
        },
        {
            "date": "2026-05-13",
            "event": "CPI Avril 2026",
            "importance": "HIGH",
            "impact": "Inflation — conditionne la politique Fed",
            "consensus": "Estimé +3.1% YoY",
        },
        {
            "date": "2026-05-02",
            "event": "NFP Avril 2026",
            "importance": "HIGH",
            "impact": "Emploi — baromètre santé économique",
            "consensus": "Estimé +185K emplois",
        },
        {
            "date": "2026-05-15",
            "event": "PPI Avril 2026",
            "importance": "MEDIUM",
            "impact": "Inflation producteurs — précurseur CPI",
            "consensus": None,
        },
        {
            "date": "2026-05-28",
            "event": "PCE Avril 2026",
            "importance": "HIGH",
            "impact": "Inflation préférée Fed — cible 2%",
            "consensus": "Estimé +2.8% YoY",
        },
        {
            "date": "2026-06-11",
            "event": "CPI Mai 2026",
            "importance": "HIGH",
            "impact": "Inflation mai — input clé pour Fed juin",
            "consensus": None,
        },
        {
            "date": "2026-06-18",
            "event": "Décision Fed (FOMC)",
            "importance": "HIGH",
            "impact": "Potentielle baisse de taux si inflation recule",
            "consensus": "Cut possible si CPI < 3%",
        },
    ]

    future = [e for e in events if date.fromisoformat(e["date"]) >= today]
    future.sort(key=lambda x: x["date"])
    for e in future:
        d = date.fromisoformat(e["date"])
        e["days_until"] = (d - today).days
        e["is_imminent"] = e["days_until"] <= 7
    return future


# ── Main entry point ──────────────────────────────────────────────

async def fetch_full_macro() -> dict:
    """Aggregates all macro data into a single dashboard payload."""
    import datetime

    cached = cache_get(_NS, "full")
    if cached:
        return cached

    from app.ml.scoring.market_context import fetch_fred_macro

    fred_base, fred_extra, markets = await asyncio.gather(
        fetch_fred_macro(),
        fetch_fred_enriched(),
        fetch_global_markets(),
        return_exceptions=True,
    )

    if isinstance(fred_base, Exception):
        logger.warning("macro_engine: fred_base failed: %s", fred_base)
        fred_base = {}
    if isinstance(fred_extra, Exception):
        logger.warning("macro_engine: fred_extra failed: %s", fred_extra)
        fred_extra = {}
    if isinstance(markets, Exception):
        logger.warning("macro_engine: markets failed: %s", markets)
        markets = {}

    regime = compute_market_regime(fred_base, fred_extra, markets)
    calendar = build_economic_calendar()

    result = {
        "regime": regime,
        "fred": {**fred_base, **fred_extra},
        "markets": markets,
        "calendar": calendar,
        "cached_at": str(datetime.datetime.utcnow()),
    }

    cache_set(_NS, "full", result, ttl=900)
    logger.info(
        "macro_engine: full macro fetched — regime=%s score=%d",
        regime["regime"], regime["score"],
    )
    return result
