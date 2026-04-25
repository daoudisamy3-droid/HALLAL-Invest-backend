"""
Smart Peer Engine – Phase 2 of the ANALYZE tab.

Flow:
  1. Resolve the source ticker's sector via yfinance.
  2. Look up the top peers in a curated sector universe (sectoral
     pertinence, already ranked by market cap).
  3. In parallel, fetch the Risk Core payload for the source ticker
     plus its 3 peers (bounded by a 10-second timeout).
  4. Compute a deterministic 0-100 risk_score for each, derive a
     human-readable delta_reason per peer, and pick the best
     risk/valuation trade-off as the "recommendation".

Aggressive 5-minute cache on both peers and compare-chart payloads.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from app.core.cache import cache_get, cache_set
from app.core.logging import logger
from app.services.analyze_service import (
    get_risk_core,
    _score_valuation as _ssot_score_valuation,
    _score_solvency as _ssot_score_solvency,
    _score_growth as _ssot_score_growth,
    _global_score as _ssot_global_score,
)

_executor = ThreadPoolExecutor(max_workers=6)

# ── Curated sector peer universe ─────────────────────────────────
#
# yfinance / OpenBB do not expose a reliable free peer endpoint, so
# we ship a curated mapping: sector → tickers roughly ranked by
# market cap. Taking the top entries (after excluding the source
# ticker) gives us "sectoral pertinence" filtering in O(1).
_SECTOR_PEERS: dict[str, list[str]] = {
    "Technology": [
        "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "AVGO", "ORCL",
        "CRM", "ADBE", "AMD", "CSCO", "QCOM", "IBM", "INTC",
    ],
    "Healthcare": [
        "LLY", "JNJ", "UNH", "NVO", "MRK", "ABBV", "TMO", "ABT", "DHR",
        "PFE", "BMY", "AMGN", "GILD", "CVS",
    ],
    "Financial Services": [
        "BRK-B", "JPM", "V", "MA", "BAC", "WFC", "GS", "MS", "BLK", "C",
        "AXP", "SCHW", "PYPL",
    ],
    "Consumer Cyclical": [
        "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "BKNG", "TJX", "SBUX",
        "ABNB", "F", "GM",
    ],
    "Consumer Defensive": [
        "WMT", "PG", "COST", "KO", "PEP", "PM", "MO", "MDLZ", "CL",
        "KHC", "TGT",
    ],
    "Communication Services": [
        "GOOGL", "META", "NFLX", "DIS", "TMUS", "VZ", "T", "CMCSA",
        "CHTR", "EA",
    ],
    "Industrials": [
        "GE", "CAT", "RTX", "HON", "UPS", "BA", "DE", "LMT", "UNP", "MMM",
        "FDX", "NOC",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "VLO", "OXY",
        "KMI", "WMB", "HES",
    ],
    "Utilities": [
        "NEE", "DUK", "SO", "AEP", "SRE", "D", "EXC", "XEL", "PEG", "ED",
    ],
    "Real Estate": [
        "PLD", "AMT", "EQIX", "CCI", "PSA", "O", "WELL", "DLR", "SPG",
        "AVB",
    ],
    "Basic Materials": [
        "LIN", "SHW", "APD", "FCX", "NEM", "ECL", "CTVA", "DOW", "NUE",
    ],
}


# ── 5-minute TTL via centralized cache ───────────────────────────

_PEER_CACHE_NS = "peers"
_CHART_CACHE_NS = "chart"
_PEER_CACHE_TTL = 300  # 5 minutes


# ── Helpers ──────────────────────────────────────────────────────


def _safe(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        f = float(val)
        if pd.isna(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _fetch_profile_blocking(symbol: str) -> dict[str, Any]:
    """
    Lightweight ticker profile: sector + long name + market cap.
    Used to resolve peer universe and enrich the response.
    """
    try:
        info = yf.Ticker(symbol).info or {}
        return {
            "sector": info.get("sector"),
            "name": info.get("longName") or info.get("shortName"),
            "market_cap": _safe(info.get("marketCap")),
        }
    except Exception as exc:
        logger.warning("peer_service: profile fetch failed for %s: %s", symbol, exc)
        return {"sector": None, "name": None, "market_cap": None}


async def _get_profile(symbol: str) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _fetch_profile_blocking, symbol)


# ── Scoring engine (SSOT — delegates to analyze_service) ─────────
#
# Unified "low = safe" semantics: 0 = safest, 100 = riskiest.
# The scoring functions are defined once in analyze_service and
# reused here to guarantee a single source of truth.


def _compute_risk_score(metrics: dict[str, Any]) -> Optional[float]:
    """
    Weighted total: 35% valuation + 35% solvency + 30% growth.
    Delegates to the SSOT scoring functions in analyze_service.
    Returns 0-100 (low = safe).
    """
    val = _ssot_score_valuation(
        metrics["valuation"].get("pe"),
        metrics["valuation"].get("industryAvgPe"),
    )
    solvency = _ssot_score_solvency(
        metrics["health"].get("debtToEquity"),
        metrics["health"].get("fcfYield"),
        metrics["health"].get("currentRatio"),
    )
    growth = _ssot_score_growth(
        metrics["growth"].get("epsGrowth3Y"),
        metrics["growth"].get("revGrowth3Y"),
    )

    score = _ssot_global_score(val, solvency, growth)
    return float(score) if score is not None else None


# ── Delta reasons (French copy for the frontend) ─────────────────


def _delta_reason(source: dict[str, Any], peer: dict[str, Any]) -> str:
    """
    Pick the most favourable differentiator for the peer and phrase it.
    Returns a short French sentence ready for the UI.
    """
    candidates: list[tuple[float, str]] = []

    # P/E — lower is better
    src_pe = source["valuation"].get("pe")
    peer_pe = peer["valuation"].get("pe")
    if src_pe and peer_pe and src_pe > 0 and peer_pe > 0 and peer_pe < src_pe:
        diff_pct = round((src_pe - peer_pe) / src_pe * 100.0, 0)
        candidates.append((diff_pct, f"P/E {int(diff_pct)}% inférieur"))

    # Debt/Equity — lower is better
    src_de = source["health"].get("debtToEquity")
    peer_de = peer["health"].get("debtToEquity")
    if src_de is not None and peer_de is not None and src_de > 0 and peer_de < src_de:
        diff_pct = round((src_de - peer_de) / src_de * 100.0, 0)
        candidates.append((diff_pct, f"Dette {int(diff_pct)}% inférieure"))

    # FCF yield — higher is better (absolute points of yield)
    src_fy = source["health"].get("fcfYield")
    peer_fy = peer["health"].get("fcfYield")
    if src_fy is not None and peer_fy is not None and peer_fy > src_fy:
        diff_pts = round(peer_fy - src_fy, 1)
        candidates.append((diff_pts * 10.0, f"FCF Yield +{diff_pts} pts"))

    # Revenue growth — higher is better
    src_rg = source["growth"].get("revGrowth3Y")
    peer_rg = peer["growth"].get("revGrowth3Y")
    if src_rg is not None and peer_rg is not None and peer_rg > src_rg:
        diff_pts = round(peer_rg - src_rg, 1)
        candidates.append((diff_pts, f"Croissance CA +{diff_pts} pts"))

    if not candidates:
        return "Profil sectoriel comparable"

    # Pick the strongest relative advantage
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


# ── Core public API ──────────────────────────────────────────────


async def _risk_core_with_timeout(symbol: str) -> dict[str, Any]:
    """Wrap get_risk_core with its own safety net so one crash ≠ all fail."""
    try:
        return await get_risk_core(symbol)
    except Exception as exc:
        logger.warning("peer_service: risk_core failed for %s: %s", symbol, exc)
        from app.services.analyze_service import _empty_payload
        return _empty_payload()


def _top_peers_for_sector(sector: Optional[str], source: str, k: int = 3) -> list[str]:
    """Return the top-k curated peers for the sector, excluding the source."""
    universe = _SECTOR_PEERS.get(sector or "", [])
    src_upper = source.upper()
    peers = [p for p in universe if p.upper() != src_upper]
    return peers[:k]


async def get_smart_peers(ticker: str) -> dict[str, Any]:
    """
    Main entry: returns the source ticker's risk score + its 3 peers
    with risk_score / metrics / delta_reason, plus a recommendation.
    """
    ticker = ticker.upper().strip()
    cached = cache_get(_PEER_CACHE_NS, ticker)
    if cached is not None:
        logger.info("peer_service: cache hit for %s", ticker)
        return cached

    # ── 1. Source profile (sector + name) ────────────────────────
    source_profile = await _get_profile(ticker)
    sector = source_profile.get("sector")

    # ── 2. Resolve peer tickers ──────────────────────────────────
    peer_symbols = _top_peers_for_sector(sector, ticker, k=3)

    # ── 3. Parallel risk_core fetch (source + peers) bounded ─────
    fetch_targets = [ticker, *peer_symbols]

    try:
        fetched = await asyncio.wait_for(
            asyncio.gather(
                *[_risk_core_with_timeout(sym) for sym in fetch_targets],
                return_exceptions=False,
            ),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        logger.error("peer_service: timeout fetching peers for %s", ticker)
        from app.services.analyze_service import _empty_payload
        fetched = [_empty_payload() for _ in fetch_targets]

    source_metrics = fetched[0]
    peer_metrics = fetched[1:]

    # ── 4. Source risk score ─────────────────────────────────────
    source_score = _compute_risk_score(source_metrics)

    # ── 5. Build peer entries in parallel ────────────────────────
    peer_profiles = await asyncio.gather(
        *[_get_profile(sym) for sym in peer_symbols],
        return_exceptions=False,
    )

    peers_out: list[dict[str, Any]] = []
    for sym, metrics, profile in zip(peer_symbols, peer_metrics, peer_profiles):
        score = _compute_risk_score(metrics)
        reason = _delta_reason(source_metrics, metrics)
        peers_out.append({
            "ticker": sym,
            "name": profile.get("name"),
            "risk_score": score,
            "metrics": metrics,
            "delta_reason": reason,
        })

    # ── 6. Recommendation: best risk/valuation trade-off ─────────
    recommendation = _pick_recommendation(peers_out, source_score)

    payload = {
        "ticker": ticker,
        "name": source_profile.get("name"),
        "sector": sector,
        "risk_score": source_score,
        "metrics": source_metrics,
        "peers": peers_out,
        "recommendation": recommendation,
    }

    cache_set(_PEER_CACHE_NS, ticker, payload, ttl=_PEER_CACHE_TTL)
    logger.info(
        "peer_service: %s sector=%s peers=%s source_score=%s reco=%s",
        ticker, sector,
        [p["ticker"] for p in peers_out],
        source_score,
        recommendation.get("ticker"),
    )
    return payload


def _pick_recommendation(
    peers: list[dict[str, Any]],
    source_score: Optional[float],
) -> dict[str, Any]:
    """
    Winner selection under strict "low = safe" semantics (0 = safest,
    100 = riskiest).

    Algorithm:
      1. Filter out every peer whose risk_score is >= source_score.
         Only peers strictly safer than the source can be recommended.
      2. composite = peer.risk_score
         If peer.metrics.valuation.pe < industryAvgPe: composite -= 10
         (a good valuation LOWERS the risk composite).
      3. Sort ascending (reverse=False); winner = candidates[0].
      4. Peers missing data:
           - risk_score = None   → excluded from the pool
           - pe = None           → no bonus applied (neutral)
    """
    if not peers:
        return {"ticker": None, "name": None, "reason": ""}

    # ── Step 1: filter to peers strictly safer than the source ──
    valid = [p for p in peers if p.get("risk_score") is not None]
    if source_score is not None:
        candidates = [p for p in valid if p["risk_score"] < source_score]
    else:
        candidates = valid

    if not candidates:
        return {
            "ticker": None,
            "name": None,
            "reason": "Aucun peer plus sûr que le ticker source",
        }

    # ── Step 2: composite (lower is better) ─────────────────────
    def _composite(p: dict[str, Any]) -> float:
        base = p["risk_score"]  # guaranteed not None by step 1 filter
        pe = p["metrics"]["valuation"].get("pe")
        ind = p["metrics"]["valuation"].get("industryAvgPe")
        val_bonus = 0.0
        # Good valuation → subtract 10 (makes composite healthier)
        if pe is not None and ind is not None and pe > 0 and ind > 0 and pe < ind:
            val_bonus = -10.0
        return base + val_bonus

    # ── Step 3: sort ascending, take index 0 ────────────────────
    candidates.sort(key=_composite)
    winner = candidates[0]

    # ── Step 4: build reason string ─────────────────────────────
    reason_parts: list[str] = []
    if winner.get("risk_score") is not None:
        reason_parts.append(f"Risque {winner['risk_score']:.0f}/100")
    if winner.get("delta_reason"):
        reason_parts.append(winner["delta_reason"])
    reason = " · ".join(reason_parts) if reason_parts else "Score risque le plus bas"

    return {
        "ticker": winner["ticker"],
        "name": winner.get("name"),
        "reason": reason,
    }


# ── Compare chart (base 100, 12 months) ──────────────────────────


def _fetch_history_blocking(symbol: str) -> Optional[pd.DataFrame]:
    """12 months of daily closes. Returns None on error."""
    try:
        hist = yf.Ticker(symbol).history(period="1y", interval="1d")
        if hist is None or hist.empty:
            return None
        return hist
    except Exception as exc:
        logger.warning("peer_service: history fetch failed for %s: %s", symbol, exc)
        return None


def _normalise_base_100(hist: pd.DataFrame) -> list[dict[str, Any]]:
    """Base-100 rebase from the first close. Returns [{date, value}, ...]."""
    if hist is None or hist.empty or "Close" not in hist.columns:
        return []
    closes = hist["Close"].dropna()
    if closes.empty:
        return []
    first = float(closes.iloc[0])
    if first <= 0:
        return []
    series: list[dict[str, Any]] = []
    for ts, px in closes.items():
        try:
            date_str = ts.strftime("%Y-%m-%d")
        except Exception:
            date_str = str(ts)[:10]
        series.append({
            "date": date_str,
            "value": round(float(px) / first * 100.0, 2),
        })
    return series


async def get_compare_chart(ticker1: str, ticker2: str) -> dict[str, Any]:
    """
    12-month base-100 compare chart for two tickers. Never raises: a
    missing series is returned as an empty array.
    """
    t1 = ticker1.upper().strip()
    t2 = ticker2.upper().strip()
    cache_key = f"{t1}|{t2}"
    cached = cache_get(_CHART_CACHE_NS, cache_key)
    if cached is not None:
        logger.info("compare_chart: cache hit for %s", cache_key)
        return cached

    loop = asyncio.get_running_loop()
    try:
        hist1, hist2, prof1, prof2 = await asyncio.wait_for(
            asyncio.gather(
                loop.run_in_executor(_executor, _fetch_history_blocking, t1),
                loop.run_in_executor(_executor, _fetch_history_blocking, t2),
                _get_profile(t1),
                _get_profile(t2),
            ),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        logger.error("compare_chart: timeout for %s vs %s", t1, t2)
        hist1 = hist2 = None
        prof1 = prof2 = {"name": None, "sector": None, "market_cap": None}

    payload = {
        "ticker1": {
            "symbol": t1,
            "name": prof1.get("name"),
            "series": _normalise_base_100(hist1) if hist1 is not None else [],
        },
        "ticker2": {
            "symbol": t2,
            "name": prof2.get("name"),
            "series": _normalise_base_100(hist2) if hist2 is not None else [],
        },
    }

    cache_set(_CHART_CACHE_NS, cache_key, payload, ttl=_PEER_CACHE_TTL)
    logger.info(
        "compare_chart: %s=%d pts, %s=%d pts",
        t1, len(payload["ticker1"]["series"]),
        t2, len(payload["ticker2"]["series"]),
    )
    return payload
