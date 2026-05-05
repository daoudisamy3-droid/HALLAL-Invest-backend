"""
Risk Intelligence Endpoint — Adaptive risk block for any ticker.

Crosses ML prediction confidence with YFinance fundamentals to produce:
  - Volatility-driven profile (Défensif / Standard / Agressif)
  - Stop-loss and take-profit levels with R/R ratio
  - Kelly position sizing (requires ≥ 5 verified predictions)
  - Conviction score 0-100
  - Contextual insight phrase (template-based, no generative AI)

Route:
    GET /risk/{symbol}
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.v1.endpoints.ticker import get_ticker
from app.core.cache import cache_get
from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.ml.predictor import get_prediction


router = APIRouter()

_ACCURACY_NAMESPACE = "accuracy"


def _volatility_profile(vol: float) -> tuple[str, float, float, float]:
    """Return (profile_name, sl_multiplier, tp_multiplier, min_rr)."""
    if vol < 0.01:
        return "Défensif", 1.2, 2.0, 1.0
    if vol < 0.025:
        return "Standard", 1.5, 2.5, 1.5
    return "Agressif", 2.0, 3.5, 2.0


def _build_insight(
    profile: str,
    vol: float,
    rsi: float | None,
    rr_warning: bool,
    kelly: float | None,
    conviction: int,
    direction: str,
) -> str:
    parts: list[str] = [
        f"Profil {profile} — volatilité journalière {vol * 100:.2f}%."
    ]

    if rsi is not None:
        if rsi > 65:
            parts.append("RSI en zone de surachat : momentum élevé, prudence sur le timing d'entrée.")
        elif rsi < 35:
            parts.append("RSI en zone de survente : potentiel de rebond, mais confirmation recommandée.")
        else:
            parts.append(f"RSI neutre ({rsi:.1f}) : pas de signal de retournement immédiat.")

    if rr_warning:
        parts.append("R/R insuffisant pour ce profil de risque — envisage d'ajuster le stop ou d'attendre une meilleure entrée.")

    if kelly is None:
        parts.append("Historique de prédictions insuffisant pour calibrer la taille de position (< 5 observations).")
    elif conviction >= 65:
        parts.append(
            f"Conviction élevée ({conviction}/100) avec signal {direction} — "
            f"dimensionnement Kelly suggéré."
        )
    elif conviction >= 45:
        parts.append(
            f"Conviction modérée ({conviction}/100) — position réduite conseillée."
        )
    else:
        parts.append(
            f"Conviction faible ({conviction}/100) — attente d'une confluence de signaux recommandée."
        )

    return " ".join(parts)


@router.get(
    "/risk/{symbol}",
    summary="Risk Intelligence Block",
    description=(
        "Computes an adaptive risk block for any ticker by crossing ML prediction "
        "confidence with YFinance fundamentals. Returns stop-loss, take-profit, "
        "R/R ratio, Kelly sizing, and a conviction score (0-100)."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def risk_intelligence(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol.replace(".", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    # ── Fetch prediction and ticker data in sequence ───────────────
    # (get_prediction already calls get_ticker internally; both are cached)
    try:
        pred = await get_prediction(symbol)
    except Exception as exc:
        logger.error("risk/%s: prediction fetch failed: %s", symbol, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not compute risk block for '{symbol}'. Please retry later.",
        )

    try:
        ticker = await get_ticker(symbol)
    except Exception as exc:
        logger.warning("risk/%s: ticker fetch failed (%s) — proceeding with nulls", symbol, exc)
        ticker = None

    # ── Extract values with silent fallbacks ──────────────────────
    current_price: float = pred["current_price"]
    volatility_10d: float = pred["volatility_10d"] or 0.015  # safe default if zero
    pred_1d = pred.get("predictions", {}).get("1d", {})
    confidence: float = pred_1d.get("confidence", 50.0)
    direction: str = pred_1d.get("direction", "UP")

    rsi: float | None = ticker.rsi_14.value if ticker else None
    w52_high: float | None = ticker.fifty_two_week_high.value if ticker else None
    w52_low: float | None = ticker.fifty_two_week_low.value if ticker else None  # noqa: F841
    max_dd: float | None = ticker.max_drawdown_5y.value if ticker else None      # noqa: F841

    # Accuracy stats from cache
    acc = cache_get(_ACCURACY_NAMESPACE, symbol)
    total_tries: int = acc["total_tries"] if acc else 0
    success_count: int = acc["success_count"] if acc else 0

    # ── Volatility profile ────────────────────────────────────────
    profile, vol_mult_sl, vol_mult_tp, rr_minimum = _volatility_profile(volatility_10d)

    # ── Stop-loss and take-profit ─────────────────────────────────
    stop_loss = round(current_price * (1 - volatility_10d * vol_mult_sl), 2)

    if w52_high and w52_high > current_price:
        distance_to_52w_high = (w52_high - current_price) / current_price
    else:
        distance_to_52w_high = volatility_10d * vol_mult_tp  # conservative fallback

    tp_base = current_price * (1 + volatility_10d * vol_mult_tp)
    tp_capped = current_price * (1 + distance_to_52w_high * 0.5)
    take_profit = round(min(tp_base, tp_capped), 2)

    risk_pct = (current_price - stop_loss) / current_price
    reward_pct = (take_profit - current_price) / current_price
    rr_ratio = round(reward_pct / risk_pct, 2) if risk_pct > 0 else 0.0
    rr_warning = rr_ratio < rr_minimum

    # ── Kelly position sizing ─────────────────────────────────────
    kelly: float | None = None
    kelly_label: str

    if total_tries < 5 or acc is None:
        kelly_label = "Données insuffisantes (< 5 prédictions)"
    else:
        win_rate = success_count / total_tries
        kelly_raw = (win_rate - (1 - win_rate)) / risk_pct if risk_pct > 0 else 0.0
        kelly = round(min(max(kelly_raw, 0.0), 0.25), 4)
        kelly_label = f"{kelly * 100:.1f}% du capital"

    # ── Conviction score (0-100) ──────────────────────────────────
    ml_score = (confidence / 100) * 40
    risk_score = (1 - risk_pct) * 30

    rsi_val = rsi if rsi is not None else 50.0
    if 35 <= rsi_val <= 65:
        rsi_score = 20.0
    else:
        rsi_score = 20.0 * (1 - abs(rsi_val - 50) / 50)

    if acc is not None and total_tries >= 5:
        win_rate = success_count / total_tries
        acc_score = win_rate * 10
    else:
        acc_score = 5.0

    conviction = int(min(max(round(ml_score + risk_score + rsi_score + acc_score), 0), 100))

    # ── Contextual insight (template) ─────────────────────────────
    insight = _build_insight(
        profile=profile,
        vol=volatility_10d,
        rsi=rsi,
        rr_warning=rr_warning,
        kelly=kelly,
        conviction=conviction,
        direction=direction,
    )

    logger.info(
        "risk/%s: profile=%s conviction=%d direction=%s rr=%.2f kelly=%s",
        symbol, profile, conviction, direction, rr_ratio, kelly,
    )

    return {
        "symbol": symbol,
        "profile": profile,
        "current_price": current_price,
        "stop_loss": stop_loss,
        "stop_loss_pct": round(-risk_pct, 6),
        "take_profit": take_profit,
        "take_profit_pct": round(reward_pct, 6),
        "rr_ratio": rr_ratio,
        "rr_warning": rr_warning,
        "rr_minimum": rr_minimum,
        "kelly": kelly,
        "kelly_label": kelly_label,
        "conviction_score": conviction,
        "insight": insight,
        "volatility_10d": volatility_10d,
        "rsi_14": rsi,
    }
