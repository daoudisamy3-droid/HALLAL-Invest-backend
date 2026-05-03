"""
Macro Engine — global market data, enriched FRED series, market regime, economic calendar.

Reuses fetch_fred_macro() from market_context.py (no duplication).
Adds: indices, FX, commodities (via _fetch_category_blocking), regime scoring, calendar.

Cache TTL:
  indices / fx / commodities — 15 min
  fred_enriched              — 1 h
  full dashboard             — 15 min
"""

import asyncio
import os
from datetime import date

import httpx

from app.core.cache import cache_get, cache_set
from app.core.logging import logger

_NS = "macro_dashboard"

# ── Ticker catalogs ───────────────────────────────────────────────

INDICES = {
    # Americas
    "sp500":   {"ticker": "^GSPC",     "label": "S&P 500",       "region": "Americas"},
    "nasdaq":  {"ticker": "^IXIC",     "label": "NASDAQ",        "region": "Americas"},
    "dow":     {"ticker": "^DJI",      "label": "Dow Jones",     "region": "Americas"},
    "russell": {"ticker": "^RUT",      "label": "Russell 2000",  "region": "Americas"},
    "tsx":     {"ticker": "^GSPTSE",   "label": "S&P TSX",       "region": "Americas"},
    "bovespa": {"ticker": "^BVSP",     "label": "Bovespa",       "region": "Americas"},
    # Europe
    "dax":     {"ticker": "^GDAXI",    "label": "DAX",           "region": "Europe"},
    "cac":     {"ticker": "^FCHI",     "label": "CAC 40",        "region": "Europe"},
    "ftse":    {"ticker": "^FTSE",     "label": "FTSE 100",      "region": "Europe"},
    "stoxx":   {"ticker": "^STOXX50E", "label": "EURO STOXX 50", "region": "Europe"},
    # Asia-Pacific
    "nikkei":  {"ticker": "^N225",     "label": "Nikkei 225",    "region": "Asia"},
    "hsi":     {"ticker": "^HSI",      "label": "Hang Seng",     "region": "Asia"},
    "sti":     {"ticker": "^STI",      "label": "STI Singapore", "region": "Asia"},
    "asx":     {"ticker": "^AXJO",     "label": "ASX 200",       "region": "Asia"},
    "kospi":   {"ticker": "^KS11",     "label": "KOSPI",         "region": "Asia"},
    "nifty":   {"ticker": "^NSEI",     "label": "NIFTY 50",      "region": "Asia"},
    "sse":     {"ticker": "000001.SS", "label": "SSE Composite", "region": "Asia"},
    # Volatility
    "vix":     {"ticker": "^VIX",      "label": "VIX",           "region": "Fear"},
}

FX = {
    # USD majors
    "eurusd": {"ticker": "EURUSD=X", "label": "EUR/USD", "group": "Majeurs"},
    "gbpusd": {"ticker": "GBPUSD=X", "label": "GBP/USD", "group": "Majeurs"},
    "usdjpy": {"ticker": "JPY=X",    "label": "USD/JPY", "group": "Majeurs"},
    "usdchf": {"ticker": "CHF=X",    "label": "USD/CHF", "group": "Majeurs"},
    "usdcad": {"ticker": "CAD=X",    "label": "USD/CAD", "group": "Majeurs"},
    "audusd": {"ticker": "AUDUSD=X", "label": "AUD/USD", "group": "Majeurs"},
    "nzdusd": {"ticker": "NZDUSD=X", "label": "NZD/USD", "group": "Majeurs"},
    "dxy":    {"ticker": "DX-Y.NYB", "label": "DXY",     "group": "Majeurs"},
    # EUR crosses
    "eurgbp": {"ticker": "EURGBP=X", "label": "EUR/GBP", "group": "EUR Cross"},
    "eurjpy": {"ticker": "EURJPY=X", "label": "EUR/JPY", "group": "EUR Cross"},
    "eurchf": {"ticker": "EURCHF=X", "label": "EUR/CHF", "group": "EUR Cross"},
    "eurcad": {"ticker": "EURCAD=X", "label": "EUR/CAD", "group": "EUR Cross"},
    "eurcny": {"ticker": "EURCNY=X", "label": "EUR/CNY", "group": "EUR Cross"},
    # Asia & EM
    "usdcny": {"ticker": "CNY=X",    "label": "USD/CNY", "group": "Asie"},
    "usdhkd": {"ticker": "USDHKD=X", "label": "USD/HKD", "group": "Asie"},
    "usdsgd": {"ticker": "USDSGD=X", "label": "USD/SGD", "group": "Asie"},
    "usdinr": {"ticker": "USDINR=X", "label": "USD/INR", "group": "Asie"},
    "usdmyr": {"ticker": "USDMYR=X", "label": "USD/MYR", "group": "Asie"},
    "usdmxn": {"ticker": "USDMXN=X", "label": "USD/MXN", "group": "Émergents"},
    "usdzar": {"ticker": "USDZAR=X", "label": "USD/ZAR", "group": "Émergents"},
    "usdbrl": {"ticker": "BRL=X",    "label": "USD/BRL", "group": "Émergents"},
}

COMMODITIES = {
    # Energy
    "oil_wti":   {"ticker": "CL=F", "label": "Pétrole WTI", "group": "Énergie",           "unit": "$/bbl"},
    "oil_brent": {"ticker": "BZ=F", "label": "Brent Crude", "group": "Énergie",           "unit": "$/bbl"},
    "natgas":    {"ticker": "NG=F", "label": "Gaz Naturel", "group": "Énergie",           "unit": "$/MMBtu"},
    # Precious metals
    "gold":      {"ticker": "GC=F", "label": "Or",          "group": "Métaux Précieux",   "unit": "$/oz"},
    "silver":    {"ticker": "SI=F", "label": "Argent",      "group": "Métaux Précieux",   "unit": "$/oz"},
    # Industrial metals
    "copper":    {"ticker": "HG=F", "label": "Cuivre",      "group": "Métaux Industriels","unit": "$/lb"},
    # Agriculture
    "wheat":     {"ticker": "KE=F", "label": "Blé",         "group": "Agriculture",       "unit": "cents/bu"},
    "corn":      {"ticker": "ZC=F", "label": "Maïs",        "group": "Agriculture",       "unit": "cents/bu"},
    "soybean":   {"ticker": "ZS=F", "label": "Soja",        "group": "Agriculture",       "unit": "cents/bu"},
    "coffee":    {"ticker": "KC=F", "label": "Café",        "group": "Agriculture",       "unit": "cents/lb"},
    "sugar":     {"ticker": "SB=F", "label": "Sucre",       "group": "Agriculture",       "unit": "cents/lb"},
    "cocoa":     {"ticker": "CC=F", "label": "Cacao",       "group": "Agriculture",       "unit": "$/t"},
    "cotton":    {"ticker": "CT=F", "label": "Coton",       "group": "Agriculture",       "unit": "cents/lb"},
    # Livestock
    "cattle":    {"ticker": "LE=F", "label": "Bœuf",        "group": "Élevage",           "unit": "cents/lb"},
    "hogs":      {"ticker": "HE=F", "label": "Porc",        "group": "Élevage",           "unit": "cents/lb"},
}

FRED_EXTRA_SERIES = {
    "yield_curve":  "T10Y2Y",  # Spread 10Y-2Y (récession si < 0)
    "treasury_10y": "DGS10",   # Taux 10Y
    "treasury_2y":  "DGS2Y",   # Taux 2Y
    "pce":          "PCEPI",   # PCE inflation (cible Fed)
    "retail_sales": "RSAFS",   # Ventes au détail
    "ism_mfg":      "MANEMP",  # Emploi manufacturier proxy
}

_FRED_BASE = "https://api.stlouisfed.org/fred/series/observations"


# ── Generic blocking fetcher ──────────────────────────────────────

def _fetch_category_blocking(tickers_dict: dict) -> dict:
    """Generic YFinance fetch for any category dict. Run in executor."""
    import yfinance as yf

    results: dict = {}
    for key, cfg in tickers_dict.items():
        try:
            t = yf.Ticker(cfg["ticker"])
            fast = t.fast_info
            price = float(fast.get("last_price") or 0)
            prev_close = float(fast.get("previous_close") or price)
            change_pct = (price - prev_close) / prev_close * 100 if prev_close else 0.0

            hist = t.history(period="3mo")
            perf_1w = perf_1m = perf_3m = None

            if not hist.empty:
                p_now = float(hist["Close"].iloc[-1])
                if price == 0:
                    price = p_now
                    if len(hist) >= 2:
                        p_prev = float(hist["Close"].iloc[-2])
                        change_pct = (p_now - p_prev) / p_prev * 100 if p_prev else 0.0
                if len(hist) >= 5:
                    perf_1w = round(
                        (p_now - float(hist["Close"].iloc[-5]))
                        / float(hist["Close"].iloc[-5]) * 100, 2
                    )
                if len(hist) >= 21:
                    perf_1m = round(
                        (p_now - float(hist["Close"].iloc[-21]))
                        / float(hist["Close"].iloc[-21]) * 100, 2
                    )
                if len(hist) >= 60:
                    perf_3m = round(
                        (p_now - float(hist["Close"].iloc[0]))
                        / float(hist["Close"].iloc[0]) * 100, 2
                    )

            entry: dict = {
                "label": cfg["label"],
                "price": round(price, 4),
                "change_pct": round(change_pct, 2),
                "perf_1w": perf_1w,
                "perf_1m": perf_1m,
                "perf_3m": perf_3m,
                "trend": "hausse" if change_pct > 0 else "baisse",
            }
            for field in ("region", "group", "unit"):
                if field in cfg:
                    entry[field] = cfg[field]

            results[key] = entry

        except Exception as exc:
            logger.warning("macro_engine/fetch/%s (%s): %s", key, cfg["ticker"], exc)
            results[key] = {
                "label": cfg.get("label", key),
                "price": None,
                "change_pct": None,
                "perf_1w": None,
                "perf_1m": None,
                "perf_3m": None,
                "trend": None,
            }

    return results


# ── Per-category async wrappers ───────────────────────────────────

async def fetch_indices() -> dict:
    cached = cache_get(_NS, "indices")
    if cached:
        return cached
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _fetch_category_blocking, INDICES)
    cache_set(_NS, "indices", result, ttl=900)
    logger.info("macro_engine: indices fetched (%d)", len(result))
    return result


async def fetch_fx() -> dict:
    cached = cache_get(_NS, "fx")
    if cached:
        return cached
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _fetch_category_blocking, FX)
    cache_set(_NS, "fx", result, ttl=900)
    logger.info("macro_engine: fx fetched (%d)", len(result))
    return result


async def fetch_commodities() -> dict:
    cached = cache_get(_NS, "commodities")
    if cached:
        return cached
    loop = asyncio.get_running_loop()
    result = await loop.run_in_executor(None, _fetch_category_blocking, COMMODITIES)
    cache_set(_NS, "commodities", result, ttl=900)
    logger.info("macro_engine: commodities fetched (%d)", len(result))
    return result


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

def compute_market_regime(fred_base: dict, fred_extra: dict, indices: dict) -> dict:
    """
    Scores the global market regime from all data sources.
    Returns RISK-ON / NEUTRE / RISK-OFF with score 0-100.
    """
    score = 50
    factors: list[str] = []
    warnings: list[str] = []

    # VIX
    vix = (indices.get("vix") or {})
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
    sp500 = (indices.get("sp500") or {})
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

    fred_base, fred_extra, indices, fx, commodities = await asyncio.gather(
        fetch_fred_macro(),
        fetch_fred_enriched(),
        fetch_indices(),
        fetch_fx(),
        fetch_commodities(),
        return_exceptions=True,
    )

    if isinstance(fred_base, Exception):
        logger.warning("macro_engine: fred_base failed: %s", fred_base)
        fred_base = {}
    if isinstance(fred_extra, Exception):
        logger.warning("macro_engine: fred_extra failed: %s", fred_extra)
        fred_extra = {}
    if isinstance(indices, Exception):
        logger.warning("macro_engine: indices failed: %s", indices)
        indices = {}
    if isinstance(fx, Exception):
        logger.warning("macro_engine: fx failed: %s", fx)
        fx = {}
    if isinstance(commodities, Exception):
        logger.warning("macro_engine: commodities failed: %s", commodities)
        commodities = {}

    regime = compute_market_regime(fred_base, fred_extra, indices)
    calendar = build_economic_calendar()

    result = {
        "regime": regime,
        "fred": {**fred_base, **fred_extra},
        "indices": indices,
        "fx": fx,
        "commodities": commodities,
        "calendar": calendar,
        "cached_at": str(datetime.datetime.utcnow()),
    }

    cache_set(_NS, "full", result, ttl=900)
    logger.info(
        "macro_engine: full macro fetched — regime=%s score=%d",
        regime["regime"], regime["score"],
    )
    return result
