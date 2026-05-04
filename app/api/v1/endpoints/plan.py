"""
Plan Endpoint — aggregated investment plan from all signal sources.

Route:
    GET /plan/{symbol}

Fetches in parallel:
  - compute_full_score()    → Piotroski, Altman, valuation, momentum, growth
  - risk_intelligence()     → stop/TP, R/R ratio, RSI, conviction
  - get_prediction()        → ML direction + confidence
  - calendar()              → earnings dates, dividend
  - compute_entry_plan()    → OHLCV-based Fibonacci, supports, DCA levels
  - fetch_market_context()  → macro (FRED) + sector (ETF/commodity)

Signal weights:
  composite_score  × 0.35
  rr_normalized    × 0.20
  pred_confidence  × 0.15
  timing_score     × 0.15
  context_score    × 0.15

Cache TTL: 1 hour.
"""

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from app.core.cache import cache_get, cache_set
from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.ml.predictor import get_prediction
from app.ml.scoring.aggregator import compute_full_score

router = APIRouter()

_PLAN_NS = "plan"
_PLAN_TTL = 3600  # 1 hour


# ── Helpers ───────────────────────────────────────────────────────

def _safe(val, default=None):
    try:
        return float(val) if val is not None else default
    except (TypeError, ValueError):
        return default


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def _rr_normalized(rr: Optional[float]) -> float:
    if rr is None:
        return 25.0
    if rr >= 3.0:
        return 100.0
    if rr >= 2.0:
        return 75.0
    if rr >= 1.5:
        return 50.0
    if rr >= 1.0:
        return 25.0
    return 0.0


def _timing_score(
    days_to_earnings: Optional[int],
    rsi: Optional[float],
    ma200_ratio: Optional[float],
) -> float:
    score = 50.0

    if days_to_earnings is not None:
        if days_to_earnings < 7:
            score -= 30
        elif days_to_earnings < 30:
            score -= 10
        else:
            score += 10

    if rsi is not None:
        if rsi < 35:
            score += 20
        elif rsi > 70:
            score -= 20

    if ma200_ratio is not None:
        if ma200_ratio > 1.0:
            score += 10
        else:
            score -= 10

    return _clamp(score)


def _verdict(signal: float) -> tuple[str, str]:
    """Returns (verdict_label, verdict_color)."""
    if signal >= 70:
        return "ENTRER", "green"
    if signal >= 55:
        return "ATTENDRE CONFIRMATION", "amber"
    if signal >= 40:
        return "SURVEILLER", "gray"
    return "ÉVITER", "red"


def _sizing(signal: float, monthly_budget: float) -> dict:
    if signal >= 70:
        pct = 0.80
        rationale = "Signal fort — 80% du budget mensuel"
    elif signal >= 55:
        pct = 0.50
        rationale = "Signal modéré — 50% du budget mensuel"
    elif signal >= 40:
        pct = 0.30
        rationale = "Signal faible — 30% du budget mensuel, position exploratoire"
    else:
        pct = 0.0
        rationale = "Signal insuffisant — pas d'entrée recommandée"

    return {
        "monthly_budget": round(monthly_budget, 2),
        "recommended_eur": round(monthly_budget * pct),
        "recommended_pct": round(pct * 100, 1),
        "rationale": rationale,
    }


def _portfolio_pnl(symbol: str, current_price: float) -> Optional[float]:
    """Check if symbol is in portfolio and compute unrealised P&L %."""
    try:
        portfolio = cache_get("portfolio", "positions") or {}
        for pos in portfolio.get("positions", []):
            if pos.get("symbol") == symbol:
                txs = pos.get("transactions", [])
                total_shares = 0.0
                total_cost = 0.0
                for t in txs:
                    if t.get("type") == "BUY":
                        total_shares += t["shares"]
                        total_cost += t["shares"] * t["price"] + t.get("fees", 0)
                    elif t.get("type") == "SELL":
                        total_shares -= t["shares"]
                if total_shares > 0 and total_cost > 0:
                    avg_price = total_cost / total_shares
                    return round((current_price - avg_price) / avg_price * 100, 1)
    except Exception:
        pass
    return None


def _build_strengths(score: dict) -> list[str]:
    strengths: list[str] = []
    sub = score.get("sub_scores", {})

    p_score = (sub.get("piotroski") or {}).get("score")
    if p_score is not None:
        if p_score >= 7:
            strengths.append(f"Solidité fondamentale élevée (Piotroski {p_score}/9)")
        elif p_score >= 5:
            strengths.append(f"Fondamentaux corrects (Piotroski {p_score}/9)")

    altman = sub.get("altman") or {}
    if altman.get("zone") == "SÛRE":
        strengths.append(f"Risque faillite faible (Z-Score {altman.get('z_score')})")

    val = sub.get("valuation") or {}
    mos = _safe(val.get("margin_of_safety_pct"))
    if mos is not None and mos > 10:
        strengths.append(f"Décote vs valeur Graham ({mos:.1f}% marge de sécurité)")

    mom = sub.get("momentum") or {}
    rsi = _safe(mom.get("rsi_14"))
    if rsi is not None and rsi < 40:
        strengths.append(f"Zone de survente (RSI {rsi:.1f})")

    growth = sub.get("growth") or {}
    rev_cagr = _safe(growth.get("revenue_growth_pct"))
    if rev_cagr is not None and rev_cagr > 10:
        strengths.append(f"Croissance CA {rev_cagr:.1f}% YoY")

    eps_cagr = _safe(growth.get("eps_growth_pct"))
    if eps_cagr is not None and eps_cagr > 10:
        strengths.append(f"Croissance EPS {eps_cagr:.1f}% YoY")

    return strengths


def _build_entry_conditions(
    symbol: str,
    signal: float,
    rsi: Optional[float],
    days_to_earnings: Optional[int],
    next_earnings_date: Optional[str],
    piotroski_score: Optional[int],
    current_price: float,
    ma50_ratio: Optional[float],
) -> list[str]:
    conditions = [
        "Vérifier statut halal sur Musaffa",
        "Placer un ordre limite, pas au marché",
    ]

    if rsi is not None and rsi > 65:
        if ma50_ratio and ma50_ratio > 0:
            ma50_price = round(current_price / ma50_ratio, 2)
            conditions.append(f"Attendre une correction vers MA50 (${ma50_price})")
        else:
            conditions.append("Attendre une correction vers la MA50")

    if days_to_earnings is not None and 0 <= days_to_earnings < 30:
        date_label = next_earnings_date or "date inconnue"
        conditions.append(
            f"Earnings le {date_label} — attendre les résultats "
            f"si position < 50% du sizing prévu"
        )

    pnl = _portfolio_pnl(symbol, current_price)
    if pnl is not None and pnl > 20:
        conditions.append(
            f"Position déjà en portefeuille +{pnl:.1f}% — "
            f"réévaluer le point d'entrée"
        )

    if piotroski_score is not None and piotroski_score < 4:
        conditions.append(
            f"Qualité fondamentale faible (F-Score {piotroski_score}/9) — "
            f"exiger une marge de sécurité supplémentaire"
        )

    return conditions


# ── Endpoint ──────────────────────────────────────────────────────

@router.get(
    "/plan/{symbol}",
    summary="Plan d'Investissement",
    description=(
        "Agrège score composite, risque, prédiction ML et calendrier en un "
        "plan structuré: signal 0-100, verdict, sizing en euros, conditions "
        "d'entrée et catalyseurs identifiés. Cache TTL 1h."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_plan(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    cached = cache_get(_PLAN_NS, symbol)
    if cached is not None:
        logger.info("plan/%s: cache hit", symbol)
        return cached

    # ── Import endpoint functions (safe: no Depends in signatures) ─
    from app.api.v1.endpoints.risk import risk_intelligence
    from app.api.v1.endpoints.calendar import calendar
    from app.ml.scoring.entry_engine import compute_entry_plan
    from app.ml.scoring.market_context import fetch_market_context

    # ── Parallel fetch of all signals ─────────────────────────────
    results = await asyncio.gather(
        compute_full_score(symbol),
        risk_intelligence(symbol),
        get_prediction(symbol),
        calendar(symbol),
        compute_entry_plan(symbol),
        return_exceptions=True,
    )

    score_raw, risk_raw, pred_raw, cal_raw, entry_raw = results

    # Safe extraction with fallback empty dicts
    score: dict = score_raw if isinstance(score_raw, dict) else {}
    risk: dict = risk_raw if isinstance(risk_raw, dict) else {}
    pred: dict = pred_raw if isinstance(pred_raw, dict) else {}
    cal: dict = cal_raw if isinstance(cal_raw, dict) else {}
    entry_plan: dict = entry_raw if isinstance(entry_raw, dict) else {"available": False}

    for name, exc in [("score", score_raw), ("risk", risk_raw),
                      ("pred", pred_raw), ("cal", cal_raw), ("entry", entry_raw)]:
        if isinstance(exc, Exception):
            logger.warning("plan/%s: %s fetch failed: %s", symbol, name, exc)

    # ── Sector for market context (from score data) ───────────────
    sector: str = (score.get("halal") or {}).get("sector") or ""
    market_ctx: dict = {"context_score": 50.0, "context_signal": "NEUTRE"}
    try:
        market_ctx = await fetch_market_context(symbol, sector)
    except Exception as exc:
        logger.warning("plan/%s: market_context failed: %s", symbol, exc)

    # ── Extract key values ─────────────────────────────────────────
    composite_score = _safe(score.get("composite_score"), 50.0)
    current_price = _safe(pred.get("current_price") or risk.get("current_price"), 0.0)

    pred_1d = (pred.get("predictions") or {}).get("1d", {})
    pred_direction = pred_1d.get("direction", "N/A")
    pred_confidence = _safe(pred_1d.get("confidence"), 50.0)

    rr_ratio = _safe(risk.get("rr_ratio"), 0.0)
    stop_loss = _safe(risk.get("stop_loss"))
    take_profit = _safe(risk.get("take_profit"))
    rsi = _safe(risk.get("rsi_14"))

    # Fallback RSI from momentum sub-score
    if rsi is None:
        rsi = _safe((score.get("sub_scores") or {}).get("momentum", {}).get("rsi_14"))

    sub_scores = score.get("sub_scores") or {}
    momentum = sub_scores.get("momentum") or {}
    ma200_ratio = _safe(momentum.get("ma200_ratio"))
    ma50_ratio = _safe(momentum.get("ma50_ratio"))

    earnings_block = cal.get("earnings") or {}
    days_to_earnings: Optional[int] = earnings_block.get("days_until")
    next_earnings_date: Optional[str] = earnings_block.get("next_date")

    dividend_block = cal.get("dividend") or {}
    next_dividend: Optional[str] = dividend_block.get("ex_date")

    piotroski_score: Optional[int] = (sub_scores.get("piotroski") or {}).get("score")

    # ── Compute derived scores ─────────────────────────────────────
    rr_norm = _rr_normalized(rr_ratio)
    timing = _timing_score(days_to_earnings, rsi, ma200_ratio)
    conf_score = pred_confidence  # already 0-100
    context_score = _safe((market_ctx or {}).get("context_score"), 50.0)

    signal_global = _clamp(
        0.35 * composite_score
        + 0.20 * rr_norm
        + 0.15 * conf_score
        + 0.15 * timing
        + 0.15 * context_score
    )
    signal_global = round(signal_global, 1)

    verdict, verdict_color = _verdict(signal_global)

    # ── Budget from portfolio settings (default 200€) ─────────────
    portfolio_data = cache_get("portfolio", "positions") or {}
    monthly_budget = _safe(
        (portfolio_data.get("settings") or {}).get("monthly_budget"), 200.0
    )
    if monthly_budget == 0.0:
        monthly_budget = 200.0

    sizing_block = _sizing(signal_global, monthly_budget)

    # ── MA200 signal label ─────────────────────────────────────────
    ma200_signal = "N/A"
    if ma200_ratio is not None:
        ma200_signal = "AU-DESSUS" if ma200_ratio > 1.0 else "EN-DESSOUS"

    # ── Entry conditions + strengths ───────────────────────────────
    entry_conditions = _build_entry_conditions(
        symbol=symbol,
        signal=signal_global,
        rsi=rsi,
        days_to_earnings=days_to_earnings,
        next_earnings_date=next_earnings_date,
        piotroski_score=piotroski_score,
        current_price=current_price,
        ma50_ratio=ma50_ratio,
    )

    strengths = _build_strengths(score)

    # ── Assemble result ────────────────────────────────────────────
    result = {
        "symbol": symbol,
        "signal_global": signal_global,
        "verdict": verdict,
        "verdict_color": verdict_color,
        "sizing": sizing_block,
        "scores_detail": {
            "composite_score": round(composite_score, 1),
            "timing_score": round(timing, 1),
            "rr_normalized": round(rr_norm, 1),
            "prediction_confidence": round(conf_score, 1),
            "context_score": round(context_score, 1),
        },
        "entry_conditions": entry_conditions,
        "catalysts": {
            "next_earnings": next_earnings_date,
            "next_dividend": next_dividend,
            "strengths": strengths,
            "stop_loss": stop_loss,
            "take_profit": take_profit,
            "risk_reward": rr_ratio,
        },
        "sub_signals": {
            "score_verdict": score.get("verdict", "N/A"),
            "prediction_direction": pred_direction,
            "prediction_confidence": round(pred_confidence, 1),
            "rsi": round(rsi, 1) if rsi is not None else None,
            "ma200_signal": ma200_signal,
            "days_to_earnings": days_to_earnings,
        },
        "entry_plan": entry_plan,
        "market_context": market_ctx,
    }

    cache_set(_PLAN_NS, symbol, result, ttl=_PLAN_TTL)
    logger.info(
        "plan/%s: signal=%.1f verdict=%s timing=%.1f rr_norm=%.1f conf=%.1f ctx=%.1f scenario=%s",
        symbol, signal_global, verdict, timing, rr_norm, conf_score,
        context_score, (entry_plan or {}).get("scenario", "N/A"),
    )
    return result
