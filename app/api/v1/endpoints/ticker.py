import asyncio
import time
from typing import Any, Optional
from urllib.parse import urlparse

import pandas as pd
from fastapi import APIRouter, HTTPException, Depends

from app.core.cache import cache_get, cache_set
from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.integration import yfinance_client
from app.ml.data_loader import fetch_ohlcv
from app.models.schemas import (
    DataGeneral, Metric, PriceResponse, AAOIFIAudit,
    AnalystSentiment, RecommendationBreakdown,
    StrategicAnalysis,
)

router = APIRouter()

_TICKER_CACHE_NS = "ticker"
_TICKER_CACHE_TTL = 86400  # 24 hours


# ── Formatting helpers ────────────────────────────────────────────

def _fmt_large(val: Optional[float]) -> str:
    """Format large numbers: 1.23T, 456.7B, 12.3M, 1.5K."""
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
    if abs_val >= 1e3:
        return f"{sign}{abs_val / 1e3:.2f}K"
    return f"{sign}{abs_val:.2f}"


def _fmt_pct(val: Optional[float]) -> str:
    """Format as percentage (value is already a ratio, e.g. 0.15 → '15.00%')."""
    if val is None:
        return "N/A"
    return f"{val * 100:.2f}%"


def _fmt_num(val: Optional[float], decimals: int = 2) -> str:
    if val is None:
        return "N/A"
    return f"{val:.{decimals}f}"


def _metric(val: Optional[float], display_value: str, source: str = "yfinance") -> Metric:
    return Metric(value=val, display_value=display_value, source=source)


def _safe_float(info: dict, key: str) -> Optional[float]:
    v = info.get(key)
    if v is None:
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


# ── RSI and Drawdown computation (no external TA lib) ────────────

def _compute_rsi(close: pd.Series, window: int = 14) -> Optional[float]:
    """Compute RSI-14 from a close price series using Wilder's smoothing."""
    if len(close) < window + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta.clip(upper=0))

    avg_gain = gain.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / window, min_periods=window, adjust=False).mean()

    last_avg_loss = avg_loss.iloc[-1]
    if last_avg_loss == 0:
        return 100.0
    rs = avg_gain.iloc[-1] / last_avg_loss
    return round(100 - (100 / (1 + rs)), 2)


def _compute_max_drawdown(close: pd.Series) -> Optional[float]:
    """Max drawdown as a negative percentage (e.g. -34.52)."""
    if len(close) < 2:
        return None
    cummax = close.cummax()
    drawdown = (close - cummax) / cummax
    return round(float(drawdown.min()) * 100, 2)


def _extract_domain(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    try:
        host = urlparse(url).hostname or ""
        return host[4:] if host.startswith("www.") else host or None
    except Exception:
        return None


# ── Endpoints ─────────────────────────────────────────────────────

@router.get(
    "/ticker/{symbol}/price",
    response_model=PriceResponse,
    summary="Lightweight price snapshot",
    description="Returns current price, change, and change percentage.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_ticker_price(symbol: str) -> PriceResponse:
    symbol = symbol.upper().strip()
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    try:
        data = await yfinance_client.get_fast_price(symbol)
    except Exception as exc:
        logger.error("Price fetch failed for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch price for '{symbol}'. Verify the symbol is correct.",
        )

    return PriceResponse(**data)


@router.get(
    "/ticker/{symbol}/aaoifi-audit",
    response_model=AAOIFIAudit,
    summary="AAOIFI Shariah Audit",
    description=(
        "Deterministic AAOIFI audit: financial screening (30% thresholds) "
        "and revenue screening (5% threshold) with Musaffa fallback. "
        "Results cached 24h to respect FMP rate limits."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_aaoifi_audit(symbol: str) -> AAOIFIAudit:
    symbol = symbol.upper().strip()
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    cached = cache_get(_TICKER_CACHE_NS, f"audit:{symbol}")
    if cached is not None:
        logger.info("AAOIFI audit cache hit for %s", symbol)
        return AAOIFIAudit(**cached)

    from app.services.shariah_audit import run_aaoifi_audit
    try:
        result = await run_aaoifi_audit(symbol)
    except Exception as exc:
        logger.error("AAOIFI audit failed for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=502,
            detail=f"AAOIFI audit failed for '{symbol}'. Please retry later.",
        )

    cache_set(_TICKER_CACHE_NS, f"audit:{symbol}", result.model_dump(), ttl=_TICKER_CACHE_TTL)
    logger.info("AAOIFI audit cached for %s (TTL=24h)", symbol)

    return result


@router.get(
    "/ticker/{symbol}/strategic-analysis",
    response_model=StrategicAnalysis,
    summary="Strategic Analysis (INFOS tab)",
    description=(
        "yfinance identity + FMP/Musaffa revenue segments + sector-based "
        "Moat/SWOT defaults. Always returns 200 with all JSON keys present."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_strategic_analysis(symbol: str) -> StrategicAnalysis:
    symbol = symbol.upper().strip()
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    cached = cache_get(_TICKER_CACHE_NS, f"strategic:{symbol}")
    if cached is not None:
        logger.info("Strategic analysis cache hit for %s", symbol)
        return StrategicAnalysis(**cached)

    from app.services.strategic_analysis import run_strategic_analysis

    try:
        result = await run_strategic_analysis(symbol)
    except Exception as exc:
        logger.error("Strategic analysis failed for %s: %s", symbol, exc)
        from datetime import datetime, timezone
        result = StrategicAnalysis(
            symbol=symbol,
            flags=["INTERNAL_ERROR"],
            source="yfinance",
            cached_at=datetime.now(timezone.utc).isoformat(),
        )

    cache_set(_TICKER_CACHE_NS, f"strategic:{symbol}", result.model_dump(), ttl=_TICKER_CACHE_TTL)
    logger.info("Strategic analysis cached for %s (TTL=24h)", symbol)

    return result


@router.get(
    "/ticker/{symbol}/analyst-sentiment",
    response_model=AnalystSentiment,
    summary="Analyst Sentiment & Upside",
    description=(
        "Returns analyst consensus (recommendationMean, recommendationKey), "
        "target price with computed upside potential, and vote breakdown "
        "(Strong Buy / Buy / Hold / Sell / Strong Sell). "
        "Flags LOW_CONFIDENCE_SAMPLE when numberOfAnalystOpinions < 5."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_analyst_sentiment(symbol: str) -> AnalystSentiment:
    symbol = symbol.upper().strip()
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    try:
        data = await yfinance_client.get_analyst_sentiment(symbol)
    except Exception as exc:
        logger.error("Analyst sentiment fetch failed for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch analyst data for '{symbol}'. Verify the symbol is correct.",
        )

    flags: list[str] = []
    cur_price = data.get("current_price")
    target = data.get("target_mean_price")
    opinions = data.get("number_of_analyst_opinions")

    # Upside calculation
    upside_pct: Optional[float] = None
    upside_display = "N/A"
    if cur_price and target and cur_price > 0:
        upside_pct = round((target - cur_price) / cur_price * 100, 2)
        sign = "+" if upside_pct >= 0 else ""
        upside_display = f"{sign}{upside_pct}%"

    # Low confidence flag
    if opinions is not None and opinions < 5:
        flags.append("LOW_CONFIDENCE_SAMPLE")

    bd = data.get("breakdown", {})
    breakdown = RecommendationBreakdown(
        strong_buy=bd.get("strong_buy", 0),
        buy=bd.get("buy", 0),
        hold=bd.get("hold", 0),
        sell=bd.get("sell", 0),
        strong_sell=bd.get("strong_sell", 0),
    )

    return AnalystSentiment(
        symbol=symbol,
        recommendation_mean=data.get("recommendation_mean"),
        recommendation_key=data.get("recommendation_key"),
        target_mean_price=target,
        current_price=cur_price,
        upside_pct=upside_pct,
        upside_display=upside_display,
        number_of_analyst_opinions=opinions,
        breakdown=breakdown,
        flags=flags,
    )


@router.get(
    "/ticker/{symbol}",
    response_model=DataGeneral,
    summary="Full ticker data",
    description=(
        "Returns identity, raw fundamentals, technicals, and computed "
        "advanced indicators (RSI-14, max drawdown 5y). Each metric "
        "includes value, display_value, and source provenance."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_ticker(symbol: str) -> DataGeneral:
    symbol = symbol.upper().strip()
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    # ── Parallel fetch: info + 5y history ─────────────────────────
    try:
        info, history = await asyncio.gather(
            yfinance_client.get_ticker_info(symbol),
            yfinance_client.get_history(symbol, period="5y", interval="1d"),
        )
    except Exception as exc:
        logger.error("Data fetch failed for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=404,
            detail=f"Could not fetch data for '{symbol}'. Verify the symbol is correct.",
        )

    close: pd.Series = history["Close"]
    src = "yfinance"

    # ── Fondamentaux (natifs) ─────────────────────────────────────
    market_cap = _safe_float(info, "marketCap")
    trailing_pe = _safe_float(info, "trailingPE")
    price_to_sales = _safe_float(info, "priceToSalesTrailing12Months")
    profit_margins = _safe_float(info, "profitMargins")
    roe = _safe_float(info, "returnOnEquity")
    roa = _safe_float(info, "returnOnAssets")
    rev_per_share = _safe_float(info, "revenuePerShare")
    div_rate = _safe_float(info, "dividendRate")

    # ── Techniques (natifs) ───────────────────────────────────────
    cur_price = _safe_float(info, "currentPrice") or _safe_float(info, "regularMarketPrice")
    mkt_change_pct = _safe_float(info, "regularMarketChangePercent")
    w52_high = _safe_float(info, "fiftyTwoWeekHigh")
    w52_low = _safe_float(info, "fiftyTwoWeekLow")
    avg_50 = _safe_float(info, "fiftyDayAverage")
    avg_200 = _safe_float(info, "twoHundredDayAverage")

    # ── Indicateurs avancés (calculés) ────────────────────────────
    rsi = _compute_rsi(close)
    max_dd = _compute_max_drawdown(close)

    return DataGeneral(
        # Identity
        symbol=symbol,
        name=info.get("longName") or info.get("shortName"),
        sector=info.get("sector"),
        industry=info.get("industry"),
        currency=info.get("currency"),
        website=_extract_domain(info.get("website")),
        # Fondamentaux
        market_cap=_metric(market_cap, _fmt_large(market_cap), src),
        trailing_pe=_metric(trailing_pe, _fmt_num(trailing_pe), src),
        price_to_sales=_metric(price_to_sales, _fmt_num(price_to_sales), src),
        profit_margins=_metric(profit_margins, _fmt_pct(profit_margins), src),
        return_on_equity=_metric(roe, _fmt_pct(roe), src),
        return_on_assets=_metric(roa, _fmt_pct(roa), src),
        revenue_per_share=_metric(rev_per_share, _fmt_num(rev_per_share), src),
        dividend_rate=_metric(div_rate, _fmt_num(div_rate), src),
        # Techniques
        current_price=_metric(cur_price, _fmt_num(cur_price), src),
        regular_market_change_pct=_metric(mkt_change_pct, _fmt_pct(mkt_change_pct / 100) if mkt_change_pct is not None else "N/A", src),
        fifty_two_week_high=_metric(w52_high, _fmt_num(w52_high), src),
        fifty_two_week_low=_metric(w52_low, _fmt_num(w52_low), src),
        fifty_day_average=_metric(avg_50, _fmt_num(avg_50), src),
        two_hundred_day_average=_metric(avg_200, _fmt_num(avg_200), src),
        # Avancés
        rsi_14=_metric(rsi, _fmt_num(rsi), "calc/yfinance"),
        max_drawdown_5y=_metric(max_dd, f"{max_dd}%" if max_dd is not None else "N/A", "calc/yfinance"),
    )


_OHLCV_CACHE_NS = "ohlcv"
_OHLCV_CACHE_TTL = 3600  # 1 hour


@router.get(
    "/ticker/{symbol}/ohlcv",
    summary="OHLCV Bars",
    description=(
        "Returns historical OHLCV bars for a symbol. "
        "`limit` controls the number of bars (default 90), "
        "`timeframe` the bar size (default '1Day')."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_ticker_ohlcv(
    symbol: str,
    limit: int = 90,
    timeframe: str = "1Day",
) -> dict:
    symbol = symbol.upper().strip()
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    cache_key = f"{symbol}:{limit}:{timeframe}"
    cached = cache_get(_OHLCV_CACHE_NS, cache_key)
    if cached is not None:
        logger.info("ohlcv/%s: cache hit (limit=%d timeframe=%s)", symbol, limit, timeframe)
        return cached

    try:
        df = await fetch_ohlcv(symbol, limit=limit, timeframe=timeframe)
    except Exception as exc:
        logger.error("ohlcv/%s: fetch failed: %s", symbol, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch OHLCV data for '{symbol}'. Please retry later.",
        )

    bars = [
        {
            "date": row["timestamp"].strftime("%Y-%m-%d"),
            "open": round(float(row["open"]), 4),
            "high": round(float(row["high"]), 4),
            "low": round(float(row["low"]), 4),
            "close": round(float(row["close"]), 4),
            "volume": int(row["volume"]),
        }
        for row in df.to_dict(orient="records")
    ]

    result = {"symbol": symbol, "bars": bars}
    cache_set(_OHLCV_CACHE_NS, cache_key, result, ttl=_OHLCV_CACHE_TTL)
    logger.info("ohlcv/%s: %d bars returned (limit=%d timeframe=%s)", symbol, len(bars), limit, timeframe)
    return result


# ── Management ────────────────────────────────────────────────────

_MGMT_CACHE_NS = "management"
_MGMT_CACHE_TTL = 86400  # 24 hours

_PARTICLES = {"de", "van", "bin", "el", "al", "du", "von", "der"}


def _normalize_rank(title: str) -> int:
    t = title.lower()
    if "chief executive" in t or "ceo" in t or "president and c" in t:
        return 1
    if "chief financial" in t or "cfo" in t:
        return 2
    if "chief operating" in t or "coo" in t:
        return 3
    if "chief technology" in t or "cto" in t:
        return 4
    if "chief product" in t or "cpo" in t:
        return 5
    if "chief marketing" in t or "cmo" in t:
        return 6
    if "general counsel" in t or "chief legal" in t:
        return 7
    if "chief human" in t or "chro" in t:
        return 8
    if "secretary" in t:
        return 9
    return 10


def _make_initials(name: str) -> str:
    words = [w for w in name.split() if w.lower() not in _PARTICLES]
    if len(words) >= 2:
        return (words[0][0] + words[-1][0]).upper()
    if len(words) == 1:
        return words[0][:2].upper()
    return ""


@router.get(
    "/ticker/{symbol}/management",
    summary="Company Management Team",
    description=(
        "Returns the company's executive officers sorted by seniority, "
        "with name, title, rank, age, compensation, and initials."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_ticker_management(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    cached = cache_get(_MGMT_CACHE_NS, symbol)
    if cached is not None:
        logger.info("management/%s: cache hit", symbol)
        return cached

    try:
        info = await yfinance_client.get_ticker_info(symbol)
    except Exception as exc:
        logger.error("management/%s: fetch failed: %s", symbol, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch management data for '{symbol}'. Please retry later.",
        )

    officers_raw: list[dict] = info.get("companyOfficers") or []

    if not officers_raw:
        result = {"symbol": symbol, "officers": [], "available": False}
        cache_set(_MGMT_CACHE_NS, symbol, result, ttl=_MGMT_CACHE_TTL)
        return result

    officers: list[dict] = []
    for o in officers_raw:
        name = o.get("name") or ""
        title = o.get("title") or ""
        officers.append({
            "name": name,
            "title": title,
            "rank": _normalize_rank(title),
            "age": o.get("age"),
            "total_pay": o.get("totalPay"),
            "year_born": o.get("yearBorn"),
            "initials": _make_initials(name),
        })

    officers.sort(key=lambda x: (x["rank"], x["name"]))

    result = {
        "symbol": symbol,
        "company_name": info.get("longName") or symbol,
        "sector": info.get("sector"),
        "employees": info.get("fullTimeEmployees"),
        "available": True,
        "officers": officers,
    }

    cache_set(_MGMT_CACHE_NS, symbol, result, ttl=_MGMT_CACHE_TTL)
    logger.info("management/%s: %d officers returned", symbol, len(officers))
    return result
