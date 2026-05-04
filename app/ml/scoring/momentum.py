"""
Momentum Score — price-based technical indicators.

Components:
  1. RSI-14 (oversold → bullish signal for halal value investors)
  2. Price vs MA200 (above → uptrend)
  3. Price vs MA50 (above → short-term trend)
  4. 52-week position (low end → value opportunity)
  5. Beta (moderate beta preferred; extreme β penalised)

Composite: 30% RSI + 25% MA200 + 20% MA50 + 15% 52w position + 10% beta
"""

from typing import Optional

from app.ml.scoring.normalizer import normalize_rsi, normalize_position_52w, normalize_linear, _clamp


def _normalize_ma_position(price: Optional[float], ma: Optional[float]) -> float:
    """
    Price / MA ratio → 0-100.

    price slightly below MA → bullish (value entry) → 60-70
    price at MA → neutral → 50
    price 10%+ above MA → extended, risk of pullback → 20-40
    price 10%+ below MA → strong downtrend → 10-30
    """
    if price is None or ma is None or ma <= 0:
        return 50.0
    ratio = price / ma
    if ratio < 0.70:
        return 15.0  # severe downtrend
    if ratio < 0.85:
        return _clamp(30.0 + (ratio - 0.70) / 0.15 * 20.0, 10.0, 50.0)
    if ratio < 0.95:
        return 50.0 + (0.95 - ratio) / 0.10 * 20.0
    if ratio <= 1.05:
        return 50.0
    if ratio <= 1.15:
        return 50.0 - (ratio - 1.05) / 0.10 * 20.0
    return max(10.0, 30.0 - normalize_linear(ratio, 1.15, 1.40) * 0.2)


def _normalize_beta(beta: Optional[float]) -> float:
    """
    Beta → 0-100.

    0.5-1.2 → ideal range for halal value investing → 70-100
    1.2-1.8 → acceptable → 40-69
    > 1.8 or < 0 → penalised → 0-39
    """
    if beta is None:
        return 50.0
    if beta < 0:
        return 20.0
    if beta <= 0.5:
        return normalize_linear(beta, 0.0, 0.5) * 0.4 + 40.0
    if beta <= 1.2:
        return normalize_linear(beta, 0.5, 1.2) * 0.3 + 70.0
    if beta <= 1.8:
        return max(40.0, 70.0 - normalize_linear(beta, 1.2, 1.8) * 0.3)
    return max(10.0, 40.0 - normalize_linear(beta, 1.8, 3.0) * 0.3)


def compute_momentum(data: dict) -> dict:
    """
    Compute momentum score from scoring data dict.

    Returns:
        {
            "rsi_14": float | None,
            "ma200_ratio": float | None,
            "ma50_ratio": float | None,
            "position_52w": float | None,
            "beta": float | None,
            "score": float (0-100),
            "available": bool,
        }
    """
    current_price: Optional[float] = data.get("current_price")
    rsi_14: Optional[float] = data.get("rsi_14")
    ma200: Optional[float] = data.get("two_hundred_day_average")
    ma50: Optional[float] = data.get("fifty_day_average")
    high_52w: Optional[float] = data.get("fifty_two_week_high")
    low_52w: Optional[float] = data.get("fifty_two_week_low")
    beta: Optional[float] = data.get("beta")

    available = current_price is not None and current_price > 0

    # 52-week position: 0.0 = at low, 1.0 = at high
    position_52w: Optional[float] = None
    if (
        high_52w is not None
        and low_52w is not None
        and high_52w > low_52w
        and current_price is not None
    ):
        position_52w = round(
            (current_price - low_52w) / (high_52w - low_52w), 4
        )

    ma200_ratio: Optional[float] = None
    if current_price is not None and ma200 and ma200 > 0:
        ma200_ratio = round(current_price / ma200, 4)

    ma50_ratio: Optional[float] = None
    if current_price is not None and ma50 and ma50 > 0:
        ma50_ratio = round(current_price / ma50, 4)

    # Component scores
    rsi_score = normalize_rsi(rsi_14)
    ma200_score = _normalize_ma_position(current_price, ma200)
    ma50_score = _normalize_ma_position(current_price, ma50)
    pos_score = normalize_position_52w(position_52w)
    beta_score = _normalize_beta(beta)

    score = (
        0.30 * rsi_score
        + 0.25 * ma200_score
        + 0.20 * ma50_score
        + 0.15 * pos_score
        + 0.10 * beta_score
    )

    return {
        "rsi_14": round(rsi_14, 2) if rsi_14 is not None else None,
        "ma200_ratio": ma200_ratio,
        "ma50_ratio": ma50_ratio,
        "position_52w": position_52w,
        "beta": beta,
        "score": round(score, 1),
        "available": available,
    }
