import asyncio
import time
from typing import Any, Optional
from urllib.parse import urlparse

import pandas as pd
from fastapi import APIRouter, HTTPException, Depends

from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.integration import yfinance_client
from app.models.schemas import (
    DataGeneral, Metric, PriceResponse, AAOIFIAudit,
    AnalystSentiment, RecommendationBreakdown,
)

router = APIRouter()

# ── 24h in-memory cache for AAOIFI audits ────────────────────────
_audit_cache: dict[str, tuple[float, AAOIFIAudit]] = {}
_CACHE_TTL = 86400  # 24 hours


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
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
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
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    # Check cache
    now = time.time()
    if symbol in _audit_cache:
        cached_at, cached_result = _audit_cache[symbol]
        if now - cached_at < _CACHE_TTL:
            logger.info("AAOIFI audit cache hit for %s", symbol)
            return cached_result

    # Run audit
    from app.services.shariah_audit import run_aaoifi_audit
    try:
        result = await run_aaoifi_audit(symbol)
    except Exception as exc:
        logger.error("AAOIFI audit failed for %s: %s", symbol, exc)
        raise HTTPException(
            status_code=502,
            detail=f"AAOIFI audit failed for '{symbol}': {exc}",
        )

    # Store in cache
    _audit_cache[symbol] = (now, result)
    logger.info("AAOIFI audit cached for %s (TTL=24h)", symbol)

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
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
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
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
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
