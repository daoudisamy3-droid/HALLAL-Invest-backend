"""
Risk Core Engine – raw fundamentals for the ANALYZE tab.

Strictly a data provider: the frontend owns the scoring logic. We just
fetch, normalise, and return the 3-block contract (valuation / health /
growth). yfinance is the single source of truth. Missing fields are
returned as None so the frontend can show null placeholders.

60-second TTL via the centralized cache (Redis-capable).
Never raises: any exception during fetch collapses to an all-null payload.
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

_executor = ThreadPoolExecutor(max_workers=4)

_RISK_CACHE_NS = "risk"
_RISK_CACHE_TTL = 60


# ── Sector P/E benchmarks (industryAvgPe surrogate) ──────────────
#
# yfinance does not expose a reliable free industry-P/E endpoint.
# We ship long-term sector averages so the frontend always has a
# deterministic benchmark to compare against.
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


# ── Helpers ──────────────────────────────────────────────────────


def _safe(val: Any) -> Optional[float]:
    """Coerce to float; return None on null/NaN/invalid."""
    if val is None:
        return None
    try:
        f = float(val)
        if pd.isna(f):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _round(val: Optional[float], decimals: int = 2) -> Optional[float]:
    if val is None:
        return None
    return round(val, decimals)


def _empty_payload() -> dict[str, Any]:
    """
    Graceful-degradation fallback returned whenever data fetch fails.
    All metrics are None EXCEPT industryAvgPe which defaults to 15.0
    so the frontend gauges can still render a neutral baseline.
    """
    return {
        "valuation": {
            "pe": None,
            "ps": None,
            "forwardPe": None,
            "industryAvgPe": 15.0,
        },
        "health": {
            "debtToEquity": None,
            "currentRatio": None,
            "fcfYield": None,
        },
        "growth": {
            "epsGrowth3Y": None,
            "revGrowth3Y": None,
        },
    }


# ── 3-year CAGR from income statement ────────────────────────────


def _cagr_from_series(latest: Any, oldest: Any, years: int) -> Optional[float]:
    """
    Compound Annual Growth Rate as a percentage (e.g. 12.5 for +12.5%/yr).
    Returns None if either value is missing, non-positive, or years <= 0.
    """
    a = _safe(latest)
    b = _safe(oldest)
    if a is None or b is None or b <= 0 or a <= 0 or years <= 0:
        return None
    try:
        cagr = (a / b) ** (1.0 / years) - 1.0
    except (ValueError, ZeroDivisionError):
        return None
    return round(cagr * 100.0, 2)


def _extract_3y_growth(ticker: yf.Ticker) -> tuple[Optional[float], Optional[float]]:
    """
    Compute 3Y EPS and revenue CAGR from yfinance annual income statement.

    yfinance typically returns 4 annual columns (current + 3 prior), sorted
    most-recent first. We take column 0 as the latest and the last column
    as the oldest, and infer the year span from the column count.
    """
    try:
        inc = ticker.income_stmt
    except Exception as exc:
        logger.debug("income_stmt fetch failed: %s", exc)
        return None, None

    if inc is None or not hasattr(inc, "empty") or inc.empty or inc.shape[1] < 2:
        return None, None

    cols = list(inc.columns)
    years = max(1, len(cols) - 1)  # e.g. 4 cols → 3 years span

    latest = inc.iloc[:, 0]
    oldest = inc.iloc[:, -1]

    # ── Revenue CAGR ─────────────────────────────────────────────
    rev_new = latest.get("Total Revenue")
    rev_old = oldest.get("Total Revenue")
    rev_growth = _cagr_from_series(rev_new, rev_old, years)

    # ── EPS CAGR (Diluted preferred, then Basic) ────────────────
    eps_new = latest.get("Diluted EPS")
    if _safe(eps_new) is None:
        eps_new = latest.get("Basic EPS")
    eps_old = oldest.get("Diluted EPS")
    if _safe(eps_old) is None:
        eps_old = oldest.get("Basic EPS")
    eps_growth = _cagr_from_series(eps_new, eps_old, years)

    return eps_growth, rev_growth


# ── Main blocking fetcher ────────────────────────────────────────


def _normalise_debt_to_equity(raw: Optional[float]) -> Optional[float]:
    """
    yfinance reports debtToEquity scaled inconsistently:
      - Most tickers: percentage form (178.92 → ratio 1.79)
      - Some tickers: decimal form already (1.79)
    Heuristic: if |raw| > 10, assume it's a percentage and divide by 100.
    """
    if raw is None:
        return None
    if abs(raw) > 10.0:
        return round(raw / 100.0, 2)
    return round(raw, 2)


def _fetch_risk_blocking(symbol: str) -> dict[str, Any]:
    """
    Blocking yfinance fetch. Returns the strict 3-block contract.

    Graceful degradation: ANY exception anywhere in this function is
    caught at the top level and collapses to the fallback shell
    (all None except industryAvgPe=15.0). The endpoint therefore
    always responds HTTP 200 with a valid contract.
    """
    try:
        # ── 1. Ticker + info ─────────────────────────────────────
        ticker = yf.Ticker(symbol)
        info = ticker.info or {}

        # ── 2. Valuation ─────────────────────────────────────────
        pe = _safe(info.get("trailingPE"))
        ps = _safe(info.get("priceToSalesTrailing12Months"))
        forward_pe = _safe(info.get("forwardPE"))
        sector = info.get("sector")
        # Default to 15.0 (neutral baseline) when sector is unknown
        industry_avg_pe = _SECTOR_PE_BENCHMARK.get(sector or "", 15.0)

        # ── 3. Health ────────────────────────────────────────────
        debt_to_equity = _normalise_debt_to_equity(_safe(info.get("debtToEquity")))
        current_ratio = _safe(info.get("currentRatio"))

        fcf = _safe(info.get("freeCashflow"))
        market_cap = _safe(info.get("marketCap"))
        fcf_yield: Optional[float] = None
        if fcf is not None and market_cap is not None and market_cap > 0:
            fcf_yield = round((fcf / market_cap) * 100.0, 2)

        # ── 4. Growth (3Y CAGR from annual income statement) ─────
        try:
            eps_growth_3y, rev_growth_3y = _extract_3y_growth(ticker)
        except Exception as growth_exc:
            # Sub-failure: keep rest of the payload, null growth only
            logger.debug("risk-core: 3Y growth calc failed for %s: %s", symbol, growth_exc)
            eps_growth_3y = None
            rev_growth_3y = None

        payload = {
            "valuation": {
                "pe": _round(pe),
                "ps": _round(ps),
                "forwardPe": _round(forward_pe),
                "industryAvgPe": industry_avg_pe,
            },
            "health": {
                "debtToEquity": debt_to_equity,
                "currentRatio": _round(current_ratio),
                "fcfYield": fcf_yield,
            },
            "growth": {
                "epsGrowth3Y": eps_growth_3y,
                "revGrowth3Y": rev_growth_3y,
            },
        }

        logger.info(
            "risk-core: %s pe=%s ps=%s fwdPe=%s d/e=%s cr=%s fcfY=%s epsG=%s revG=%s",
            symbol, pe, ps, forward_pe, debt_to_equity, current_ratio,
            fcf_yield, eps_growth_3y, rev_growth_3y,
        )
        return payload

    except Exception as e:
        # Top-level safety net: yfinance / network / parsing / anything.
        # We log loudly and return the all-null fallback so the frontend
        # can still render the scorecard at zero.
        logger.error("risk-core: fatal fetch error for %s: %s", symbol, e)
        return _empty_payload()


# ── Public async API ─────────────────────────────────────────────


async def get_risk_core(symbol: str) -> dict[str, Any]:
    """
    Fetch the Risk Core contract (valuation / health / growth).
    60s centralized cache. Never raises – returns all-null shell on error.
    """
    cached = cache_get(_RISK_CACHE_NS, symbol)
    if cached is not None:
        logger.info("risk-core: cache hit for %s", symbol)
        return cached

    loop = asyncio.get_running_loop()
    try:
        data = await loop.run_in_executor(_executor, _fetch_risk_blocking, symbol)
    except Exception as exc:
        logger.error("risk-core: unexpected error for %s: %s", symbol, exc)
        data = _empty_payload()

    cache_set(_RISK_CACHE_NS, symbol, data, ttl=_RISK_CACHE_TTL)
    return data


# ── Scorecard Engine – PROD contract ─────────────────────────────
#
# Semantics: "low = safe" on a 0-100 scale.
#   0-39   → LOW_RISK
#   40-69  → MEDIUM_RISK
#   70-100 → HIGH_RISK
#
# Pillar weights when computing the global score:
#   valuation 35% · solvency 35% · growth 30%
# Missing pillars are renormalised so the global score stays meaningful
# even when some sub-metrics are unavailable.


def _score_valuation(pe: Optional[float], industry_avg: Optional[float]) -> Optional[int]:
    """
    P/E relative to industry average.
    ratio 1.0 → 50 (neutral)   ratio 0.5 → 30 (cheap, safer)
    ratio 2.0 → 90 (expensive, riskier)
    """
    if pe is None or industry_avg is None or pe <= 0 or industry_avg <= 0:
        return None
    ratio = pe / industry_avg
    raw = 50.0 + (ratio - 1.0) * 40.0
    return int(max(0, min(100, round(raw))))


def _score_solvency(
    debt_to_equity: Optional[float],
    fcf_yield: Optional[float],
    current_ratio: Optional[float],
) -> Optional[int]:
    """
    Blended solvency score from D/E, FCF yield and current ratio.
    Lower is safer. Missing sub-metrics are skipped and the remainder
    is averaged.
    """
    parts: list[float] = []
    if debt_to_equity is not None:
        # D/E: 0 → 0, 1 → 40, 2 → 80, >2.5 → 100
        parts.append(max(0.0, min(100.0, debt_to_equity * 40.0)))
    if fcf_yield is not None:
        # FCF yield %: 0% → 70, 5% → 30, 10%+ → 0
        parts.append(max(0.0, min(100.0, 70.0 - fcf_yield * 8.0)))
    if current_ratio is not None:
        # Current ratio: <1 → 80+, 2 → 30, >=3 → 0
        parts.append(max(0.0, min(100.0, (2.0 - current_ratio) * 35.0 + 30.0)))
    if not parts:
        return None
    return int(round(sum(parts) / len(parts)))


def _score_growth(
    eps_growth_3y: Optional[float],
    rev_growth_3y: Optional[float],
) -> Optional[int]:
    """
    3Y CAGR score. Positive growth reduces risk, contraction raises it.
    +15% → ~15,  0% → 60,  -10% → ~90.
    """
    parts: list[float] = []
    for g in (eps_growth_3y, rev_growth_3y):
        if g is None:
            continue
        raw = 60.0 - g * 3.0
        parts.append(max(0.0, min(100.0, raw)))
    if not parts:
        return None
    return int(round(sum(parts) / len(parts)))


def _global_score(
    val_score: Optional[int],
    sol_score: Optional[int],
    growth_score: Optional[int],
) -> Optional[int]:
    """
    Weighted composite with null-aware renormalisation.
    Weights: valuation 35% · solvency 35% · growth 30%.
    """
    weighted: list[tuple[float, int]] = []
    if val_score is not None:
        weighted.append((0.35, val_score))
    if sol_score is not None:
        weighted.append((0.35, sol_score))
    if growth_score is not None:
        weighted.append((0.30, growth_score))
    if not weighted:
        return None
    total_w = sum(w for w, _ in weighted)
    if total_w <= 0:
        return None
    composite = sum(w * s for w, s in weighted) / total_w
    return int(round(max(0.0, min(100.0, composite))))


def _status_for(score: Optional[int]) -> str:
    if score is None:
        return "UNKNOWN"
    if score < 40:
        return "LOW_RISK"
    if score < 70:
        return "MEDIUM_RISK"
    return "HIGH_RISK"


def _build_scorecard(ticker: str, raw: dict[str, Any]) -> dict[str, Any]:
    """
    Transform the raw 3-block Risk Core payload into the strict PROD
    scorecard contract expected by the ANALYZE tab.
    """
    val = raw.get("valuation", {}) or {}
    health = raw.get("health", {}) or {}
    growth = raw.get("growth", {}) or {}

    pe_ratio = val.get("pe")
    industry_avg = val.get("industryAvgPe")
    debt_to_equity = health.get("debtToEquity")
    fcf_yield = health.get("fcfYield")
    current_ratio = health.get("currentRatio")
    eps_growth_3y = growth.get("epsGrowth3Y")
    rev_growth_3y = growth.get("revGrowth3Y")

    val_score = _score_valuation(pe_ratio, industry_avg)
    sol_score = _score_solvency(debt_to_equity, fcf_yield, current_ratio)
    growth_score = _score_growth(eps_growth_3y, rev_growth_3y)
    global_score = _global_score(val_score, sol_score, growth_score)
    status = _status_for(global_score)

    return {
        "ticker": ticker,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "risk_engine": {
            "global_score": global_score,
            "status": status,
            "pillars": {
                "valuation": {
                    "score": val_score,
                    "pe_ratio": pe_ratio,
                    "industry_avg": industry_avg,
                },
                "solvency": {
                    "score": sol_score,
                    "debt_to_equity": debt_to_equity,
                    "fcf_yield": fcf_yield,
                },
                "growth": {
                    "score": growth_score,
                    "eps_growth_3y": eps_growth_3y,
                },
            },
        },
    }


def _empty_scorecard(ticker: str) -> dict[str, Any]:
    """Fallback scorecard when raw data cannot be fetched at all."""
    return _build_scorecard(ticker, _empty_payload())


async def get_risk_scorecard(ticker: str) -> dict[str, Any]:
    """
    Fetch raw Risk Core data and fold it into the strict PROD contract
    `{ticker, timestamp, risk_engine: {global_score, status, pillars}}`.
    Never raises: falls back to an all-null scorecard on any error.
    """
    try:
        raw = await get_risk_core(ticker)
    except Exception as exc:
        logger.error("risk-scorecard: raw fetch failed for %s: %s", ticker, exc)
        return _empty_scorecard(ticker)

    try:
        return _build_scorecard(ticker, raw)
    except Exception as exc:
        logger.error("risk-scorecard: build failed for %s: %s", ticker, exc)
        return _empty_scorecard(ticker)
