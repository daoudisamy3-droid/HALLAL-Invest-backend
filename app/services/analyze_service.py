"""
Analyze engine – Risk / Earnings / Peers.

Single Source of Truth for the ANALYZE tab. Uses yfinance for all raw
fundamentals and computes deterministic scores:

  Risk total (0-100) = 0.35 * valuation + 0.35 * health + 0.30 * growth
    - valuation : P/E relative to a sector benchmark
    - health    : net margin + ROE (equal-weighted)
    - growth    : earnings growth (fallback: revenue growth)

Never raises: missing data → None in the corresponding field. All heavy
calls run in a thread pool to keep the FastAPI loop non-blocking.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any, Optional

import pandas as pd
import yfinance as yf

from app.core.logging import logger

_executor = ThreadPoolExecutor(max_workers=4)


# ── Sector defaults ──────────────────────────────────────────────
#
# Baseline P/E for the valuation score. Figures are long-term
# averages; they act only as a deterministic benchmark so the
# score is reproducible without external API calls.
_SECTOR_PE_BENCHMARK: dict[str, float] = {
    "Technology": 28.0,
    "Healthcare": 22.0,
    "Financial Services": 14.0,
    "Consumer Cyclical": 20.0,
    "Consumer Defensive": 22.0,
    "Communication Services": 18.0,
    "Industrials": 20.0,
    "Energy": 12.0,
    "Utilities": 18.0,
    "Real Estate": 25.0,
    "Basic Materials": 16.0,
}
_DEFAULT_SECTOR_PE = 20.0


# ── Curated peer universe ────────────────────────────────────────
#
# yfinance does not expose a reliable free peer endpoint, so we use
# a curated mapping: sector → list of large-cap tickers. For a given
# ticker we fetch peers in the same sector, rank them, and return
# the top 3 alternatives (excluding the source ticker itself).
_SECTOR_PEERS: dict[str, list[str]] = {
    "Technology": [
        "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "ORCL",
        "CRM", "ADBE", "AMD", "INTC", "CSCO", "QCOM", "IBM",
    ],
    "Healthcare": [
        "JNJ", "UNH", "LLY", "PFE", "MRK", "ABBV", "TMO", "ABT", "DHR",
        "BMY", "AMGN", "GILD", "CVS", "NVO",
    ],
    "Financial Services": [
        "JPM", "BAC", "WFC", "GS", "MS", "C", "V", "MA", "AXP", "BLK",
        "SCHW", "PYPL", "BRK-B",
    ],
    "Consumer Cyclical": [
        "AMZN", "TSLA", "HD", "MCD", "NKE", "LOW", "SBUX", "TJX", "BKNG",
        "ABNB", "F", "GM",
    ],
    "Consumer Defensive": [
        "WMT", "PG", "KO", "PEP", "COST", "PM", "MO", "CL", "MDLZ",
        "KHC", "TGT", "KR",
    ],
    "Communication Services": [
        "GOOGL", "META", "DIS", "NFLX", "VZ", "T", "CMCSA", "TMUS",
        "CHTR", "EA",
    ],
    "Industrials": [
        "GE", "RTX", "BA", "CAT", "HON", "UPS", "LMT", "DE", "UNP",
        "MMM", "FDX", "NOC",
    ],
    "Energy": [
        "XOM", "CVX", "COP", "SLB", "EOG", "PSX", "MPC", "VLO", "OXY",
        "KMI", "WMB", "HES",
    ],
    "Utilities": [
        "NEE", "DUK", "SO", "AEP", "D", "EXC", "SRE", "XEL", "PEG", "ED",
    ],
    "Real Estate": [
        "PLD", "AMT", "CCI", "EQIX", "PSA", "O", "WELL", "DLR", "SPG",
        "AVB",
    ],
    "Basic Materials": [
        "LIN", "FCX", "NEM", "APD", "SHW", "CTVA", "DOW", "NUE", "ECL",
    ],
}


# ── 60-second TTL cache ──────────────────────────────────────────

_CACHE_TTL = 60  # seconds
_risk_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_earnings_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_peers_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _cache_get(store: dict, key: str) -> Optional[dict[str, Any]]:
    entry = store.get(key)
    if entry is None:
        return None
    ts, data = entry
    if (datetime.now(timezone.utc).timestamp() - ts) > _CACHE_TTL:
        return None
    return data


def _cache_set(store: dict, key: str, data: dict[str, Any]) -> None:
    store[key] = (datetime.now(timezone.utc).timestamp(), data)


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


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def _fmt_pct(val: Optional[float]) -> str:
    if val is None:
        return "N/A"
    return f"{val * 100:.2f}%"


def _fmt_num(val: Optional[float], decimals: int = 2) -> str:
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Scoring engine ───────────────────────────────────────────────


def _score_valuation(pe: Optional[float], sector_pe: float) -> Optional[float]:
    """
    P/E score (0-100). Lower P/E relative to sector → higher score.
    Linear map: P/E = 0 → 100; P/E = sector_pe → 50; P/E >= 2*sector_pe → 0.
    Negative or null P/E → None (not a penalty, just missing).
    """
    if pe is None or pe <= 0:
        return None
    ratio = pe / sector_pe  # 1.0 at benchmark
    # Map ratio: 0 → 100, 1 → 50, 2 → 0
    score = 100.0 - 50.0 * ratio
    return round(_clamp(score), 2)


def _score_health(net_margin: Optional[float], roe: Optional[float]) -> Optional[float]:
    """
    Health score: average of margin and ROE sub-scores.
    Returns None if both inputs are missing.
    """
    sub: list[float] = []

    # Net margin: 0 → 0, 10% → 50, 20%+ → 100
    if net_margin is not None:
        sub.append(_clamp(net_margin * 500.0))  # 0.20 → 100

    # ROE: 0 → 0, 15% → 50, 30%+ → 100
    if roe is not None:
        sub.append(_clamp(roe * 333.33))  # 0.30 → 100

    if not sub:
        return None
    return round(sum(sub) / len(sub), 2)


def _score_growth(growth: Optional[float]) -> Optional[float]:
    """
    Growth score: earningsGrowth (fallback revenueGrowth).
    -20% → 0, 0% → 40, 10% → 70, 25%+ → 100.
    """
    if growth is None:
        return None
    # Piecewise linear
    if growth <= -0.20:
        return 0.0
    if growth <= 0.0:
        # -0.20 → 0 ; 0 → 40
        return round(_clamp(40.0 * (growth + 0.20) / 0.20), 2)
    if growth <= 0.10:
        # 0 → 40 ; 0.10 → 70
        return round(_clamp(40.0 + 300.0 * growth), 2)
    if growth <= 0.25:
        # 0.10 → 70 ; 0.25 → 100
        return round(_clamp(70.0 + 200.0 * (growth - 0.10)), 2)
    return 100.0


def _total_risk_score(
    valuation: Optional[float],
    health: Optional[float],
    growth: Optional[float],
) -> Optional[float]:
    """
    Weighted total: 35% valuation + 35% health + 30% growth.
    If a sub-score is missing, its weight is redistributed proportionally
    across the remaining sub-scores, so a partial score is still returned.
    """
    components: list[tuple[float, float]] = []  # (weight, score)
    if valuation is not None:
        components.append((0.35, valuation))
    if health is not None:
        components.append((0.35, health))
    if growth is not None:
        components.append((0.30, growth))

    if not components:
        return None

    total_weight = sum(w for w, _ in components)
    weighted = sum(w * s for w, s in components)
    return round(weighted / total_weight, 2)


# ── Blocking fetchers ────────────────────────────────────────────


def _fetch_info(symbol: str) -> dict[str, Any]:
    ticker = yf.Ticker(symbol)
    info = ticker.info or {}
    return info


def _fetch_calendar(symbol: str) -> Optional[dict[str, Any]]:
    """
    Returns calendar dict from yfinance. yfinance exposes a dict-style
    `.calendar` in recent versions (0.2.x) with keys like
    "Earnings Date", "Earnings Average", "Earnings Low", "Earnings High",
    "Revenue Average".
    """
    try:
        ticker = yf.Ticker(symbol)
        cal = ticker.calendar
        if cal is None:
            return None
        if isinstance(cal, pd.DataFrame):
            if cal.empty:
                return None
            # Older yfinance: columns=dates, rows=fields. Convert.
            return {row: cal.iloc[i, 0] for i, row in enumerate(cal.index)}
        if isinstance(cal, dict):
            return cal
    except Exception as exc:
        logger.debug("yfinance calendar fetch failed for %s: %s", symbol, exc)
    return None


def _fetch_peer_snapshot(symbol: str) -> dict[str, Any]:
    """
    Lightweight snapshot for a peer candidate:
    returns name, roe, pe, market_cap. Never raises.
    """
    try:
        info = yf.Ticker(symbol).info or {}
    except Exception:
        return {"symbol": symbol, "name": None, "roe": None, "pe": None, "market_cap": None}

    return {
        "symbol": symbol,
        "name": info.get("longName") or info.get("shortName"),
        "roe": _safe(info.get("returnOnEquity")),
        "pe": _safe(info.get("trailingPE") or info.get("forwardPE")),
        "market_cap": _safe(info.get("marketCap")),
    }


# ── Public async API ─────────────────────────────────────────────


async def get_risk_analysis(symbol: str) -> dict[str, Any]:
    """
    Risk engine: fetches ROE, net margin, P/E and returns a
    deterministic 0-100 score + sub-scores.
    """
    cached = _cache_get(_risk_cache, symbol)
    if cached is not None:
        logger.info("risk: cache hit for %s", symbol)
        return cached

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(_executor, _fetch_info, symbol)

    flags: list[str] = []

    roe = _safe(info.get("returnOnEquity"))
    net_margin = _safe(info.get("profitMargins"))
    pe_ratio = _safe(info.get("trailingPE"))
    if pe_ratio is None:
        pe_ratio = _safe(info.get("forwardPE"))
    sector = info.get("sector")
    sector_pe = _SECTOR_PE_BENCHMARK.get(sector or "", _DEFAULT_SECTOR_PE)

    # Growth: earningsGrowth first, then revenueGrowth
    growth = _safe(info.get("earningsGrowth"))
    if growth is None:
        growth = _safe(info.get("revenueGrowth"))

    val_score = _score_valuation(pe_ratio, sector_pe)
    health_score = _score_health(net_margin, roe)
    growth_score = _score_growth(growth)
    total = _total_risk_score(val_score, health_score, growth_score)

    if val_score is None:
        flags.append("MISSING_VALUATION")
    if health_score is None:
        flags.append("MISSING_HEALTH")
    if growth_score is None:
        flags.append("MISSING_GROWTH")
    if total is None:
        flags.append("INSUFFICIENT_DATA")

    payload = {
        "symbol": symbol,
        "total_score": total,
        "sub_scores": {
            "valuation": val_score,
            "health": health_score,
            "growth": growth_score,
        },
        "roe": {"value": roe, "display_value": _fmt_pct(roe)},
        "net_margin": {"value": net_margin, "display_value": _fmt_pct(net_margin)},
        "pe_ratio": {"value": pe_ratio, "display_value": _fmt_num(pe_ratio)},
        "sector": sector,
        "sector_pe_benchmark": sector_pe,
        "flags": flags,
        "source": "yfinance",
        "cached_at": _now_iso(),
    }

    _cache_set(_risk_cache, symbol, payload)
    logger.info(
        "risk: %s total=%s val=%s health=%s growth=%s",
        symbol, total, val_score, health_score, growth_score,
    )
    return payload


def _coerce_date(val: Any) -> Optional[str]:
    """Best-effort ISO date from a yfinance calendar value."""
    if val is None:
        return None
    # yfinance sometimes returns a list of dates
    if isinstance(val, list):
        for item in val:
            out = _coerce_date(item)
            if out:
                return out
        return None
    if isinstance(val, (pd.Timestamp, datetime)):
        try:
            return val.date().isoformat()
        except Exception:
            return None
    if isinstance(val, str):
        return val
    return None


async def get_earnings_calendar(symbol: str) -> dict[str, Any]:
    """
    Earnings engine: next earnings date + consensus EPS.
    what_to_watch is returned empty for now (future LLM hook).
    """
    cached = _cache_get(_earnings_cache, symbol)
    if cached is not None:
        logger.info("earnings: cache hit for %s", symbol)
        return cached

    loop = asyncio.get_running_loop()
    cal, info = await asyncio.gather(
        loop.run_in_executor(_executor, _fetch_calendar, symbol),
        loop.run_in_executor(_executor, _fetch_info, symbol),
    )

    flags: list[str] = []
    next_date: Optional[str] = None
    consensus_eps: Optional[float] = None
    consensus_eps_low: Optional[float] = None
    consensus_eps_high: Optional[float] = None
    revenue_estimate: Optional[float] = None

    if cal:
        next_date = _coerce_date(
            cal.get("Earnings Date") or cal.get("earningsDate")
        )
        consensus_eps = _safe(cal.get("Earnings Average") or cal.get("earningsAverage"))
        consensus_eps_low = _safe(cal.get("Earnings Low") or cal.get("earningsLow"))
        consensus_eps_high = _safe(cal.get("Earnings High") or cal.get("earningsHigh"))
        revenue_estimate = _safe(cal.get("Revenue Average") or cal.get("revenueAverage"))

    # Fallback next date from info
    if next_date is None and info:
        next_date = _coerce_date(info.get("earningsTimestamp")) \
            or _coerce_date(info.get("earningsDate"))

    if next_date is None:
        flags.append("MISSING_DATE")
    if consensus_eps is None:
        flags.append("MISSING_CONSENSUS")

    payload = {
        "symbol": symbol,
        "next_earnings_date": next_date,
        "consensus_eps": consensus_eps,
        "consensus_eps_low": consensus_eps_low,
        "consensus_eps_high": consensus_eps_high,
        "revenue_estimate": revenue_estimate,
        "what_to_watch": [],
        "flags": flags,
        "source": "yfinance",
        "cached_at": _now_iso(),
    }

    _cache_set(_earnings_cache, symbol, payload)
    logger.info("earnings: %s next=%s eps=%s", symbol, next_date, consensus_eps)
    return payload


def _peer_score(entry: dict[str, Any]) -> float:
    """
    Ranking formula for peers: higher ROE + lower P/E → higher rank.
    Returns a sortable composite in [0, 100].
    """
    roe = entry.get("roe")
    pe = entry.get("pe")

    # ROE score (cap 30% = 100)
    roe_score = _clamp((roe or 0.0) * 333.33) if roe is not None else 0.0

    # Valuation score
    pe_score = 50.0
    if pe is not None and pe > 0:
        pe_score = _clamp(100.0 - 50.0 * (pe / _DEFAULT_SECTOR_PE))

    return round(0.6 * roe_score + 0.4 * pe_score, 2)


async def get_peers_analysis(symbol: str) -> dict[str, Any]:
    """
    Peers engine: curated sector peer list + top 3 alternatives.
    """
    cached = _cache_get(_peers_cache, symbol)
    if cached is not None:
        logger.info("peers: cache hit for %s", symbol)
        return cached

    loop = asyncio.get_running_loop()
    info = await loop.run_in_executor(_executor, _fetch_info, symbol)

    flags: list[str] = []
    sector = info.get("sector")
    peer_universe = _SECTOR_PEERS.get(sector or "", [])

    # Exclude the source ticker itself (case-insensitive)
    sym_upper = symbol.upper()
    candidates = [p for p in peer_universe if p.upper() != sym_upper]

    if not candidates:
        flags.append("NO_SECTOR_PEERS")
        payload = {
            "symbol": symbol,
            "sector": sector,
            "peers": [],
            "top_alternatives": [],
            "flags": flags,
            "source": "yfinance",
            "cached_at": _now_iso(),
        }
        _cache_set(_peers_cache, symbol, payload)
        return payload

    # Cap candidates to keep latency bounded
    candidates = candidates[:10]

    # Parallel fetch of peer snapshots
    snapshots = await asyncio.gather(
        *[loop.run_in_executor(_executor, _fetch_peer_snapshot, p) for p in candidates],
        return_exceptions=False,
    )

    peers: list[dict[str, Any]] = []
    for snap in snapshots:
        score = _peer_score(snap)
        peers.append({
            "symbol": snap["symbol"],
            "name": snap.get("name"),
            "roe": snap.get("roe"),
            "pe_ratio": snap.get("pe"),
            "market_cap": snap.get("market_cap"),
            "score": score,
        })

    # Rank by composite score desc
    peers.sort(key=lambda p: p["score"] or 0.0, reverse=True)
    top_3 = peers[:3]

    if len(peers) < 3:
        flags.append("LOW_PEER_COVERAGE")

    payload = {
        "symbol": symbol,
        "sector": sector,
        "peers": peers,
        "top_alternatives": top_3,
        "flags": flags,
        "source": "yfinance",
        "cached_at": _now_iso(),
    }

    _cache_set(_peers_cache, symbol, payload)
    logger.info(
        "peers: %s sector=%s candidates=%d top=%s",
        symbol, sector, len(peers), [p["symbol"] for p in top_3],
    )
    return payload
