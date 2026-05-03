"""
Entry Engine — OHLCV-based entry level computation.

Computes real support/resistance levels, Fibonacci retracements, ATR,
and a scenario-driven DCA plan for any ticker.

Requires Alpaca OHLCV data via fetch_ohlcv().
Returns {"available": False} gracefully if data unavailable.
"""

import asyncio
from typing import Optional

import numpy as np
import pandas as pd

from app.core.logging import logger
from app.ml.data_loader import fetch_ohlcv


# ── Technical indicators ──────────────────────────────────────────

def compute_atr(df: pd.DataFrame, period: int = 14) -> float:
    """Average True Range over `period` days."""
    if len(df) < period + 1:
        return float(df["close"].iloc[-1] * 0.015)  # ~1.5% fallback

    high = df["high"]
    low = df["low"]
    prev_close = df["close"].shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)

    return round(float(tr.rolling(period).mean().iloc[-1]), 4)


def compute_fibonacci_levels(df: pd.DataFrame) -> dict:
    """
    Fibonacci retracements from the 52-week range (up to 252 candles).
    swing_high/swing_low based on close prices.
    """
    window = min(252, len(df))
    subset = df["close"].tail(window)

    swing_high = float(subset.max())
    swing_low = float(subset.min())
    diff = swing_high - swing_low

    def _fib(ratio: float) -> float:
        return round(swing_low + ratio * diff, 2)

    return {
        "swing_high": round(swing_high, 2),
        "swing_low": round(swing_low, 2),
        "diff": round(diff, 2),
        "fib_0": round(swing_low, 2),
        "fib_236": _fib(0.236),
        "fib_382": _fib(0.382),
        "fib_500": _fib(0.500),
        "fib_618": _fib(0.618),
        "fib_786": _fib(0.786),
        "fib_100": round(swing_high, 2),
    }


def find_support_levels(df: pd.DataFrame, lookback: int = 90) -> list[float]:
    """
    Identifies historically-validated support levels using pivot lows
    confirmed by above-average volume.

    Returns up to 3 supports sorted by proximity to the current price.
    """
    if len(df) < lookback + 2:
        return []

    subset = df.tail(lookback + 2).reset_index(drop=True)
    current_price = float(df["close"].iloc[-1])
    avg_vol = float(subset["volume"].mean())

    pivots: list[float] = []
    # Skip first and last row (need i-1 and i+1)
    for i in range(1, len(subset) - 1):
        c_prev = float(subset["close"].iloc[i - 1])
        c_curr = float(subset["close"].iloc[i])
        c_next = float(subset["close"].iloc[i + 1])
        vol = float(subset["volume"].iloc[i])

        if c_curr < c_prev and c_curr < c_next and vol > 1.2 * avg_vol:
            pivots.append(c_curr)

    if not pivots:
        return []

    # Cluster pivots within 2% of each other
    pivots_sorted = sorted(pivots)
    clusters: list[list[float]] = []
    current_cluster: list[float] = [pivots_sorted[0]]

    for price in pivots_sorted[1:]:
        if abs(price - current_cluster[-1]) / current_cluster[-1] < 0.02:
            current_cluster.append(price)
        else:
            clusters.append(current_cluster)
            current_cluster = [price]
    clusters.append(current_cluster)

    # Take median of each cluster
    level_prices = [float(np.median(c)) for c in clusters]

    # Sort by proximity to current price, return top 3
    level_prices.sort(key=lambda p: abs(p - current_price))
    return [round(p, 2) for p in level_prices[:3]]


def determine_scenario(df: pd.DataFrame, ma50: float, ma200: float) -> str:
    """
    TENDANCE_HAUSSIÈRE : price > ma200 AND ma50 > ma200 (confirmed uptrend)
    CORRECTION         : price > ma200 but ma50 <= ma200 (recovering / pull-back)
    SOUS_MA200         : price < ma200 (downtrend)
    """
    current_price = float(df["close"].iloc[-1])
    if current_price > ma200 and ma50 > ma200:
        return "TENDANCE_HAUSSIÈRE"
    if current_price < ma200:
        return "SOUS_MA200"
    return "CORRECTION"


# ── Main entry plan ───────────────────────────────────────────────

async def compute_entry_plan(symbol: str, sector: str = "") -> dict:
    """
    Computes scenario-based entry levels, Fibonacci, ATR, and DCA plan
    from 300 days of OHLCV data.

    Returns {"available": False} if Alpaca data is unavailable.
    """
    try:
        df = await fetch_ohlcv(symbol, limit=300)
    except Exception as exc:
        logger.warning("entry_engine/%s: fetch_ohlcv failed: %s", symbol, exc)
        return {"available": False}

    if df.empty or len(df) < 50:
        logger.warning("entry_engine/%s: insufficient data (%d rows)", symbol, len(df))
        return {"available": False}

    # ── Base calculations ─────────────────────────────────────────
    closes = df["close"]
    current_price = round(float(closes.iloc[-1]), 4)
    ma50 = round(float(closes.rolling(50).mean().iloc[-1]), 4)
    ma200_series = closes.rolling(200).mean()
    # Use last available value if not enough data for full 200-day window
    ma200 = round(float(ma200_series.dropna().iloc[-1]) if not ma200_series.dropna().empty else ma50, 4)

    atr = compute_atr(df)
    fibs = compute_fibonacci_levels(df)
    supports = find_support_levels(df)
    scenario = determine_scenario(df, ma50, ma200)

    logger.info(
        "entry_engine/%s: scenario=%s price=%.2f ma50=%.2f ma200=%.2f atr=%.4f",
        symbol, scenario, current_price, ma50, ma200, atr,
    )

    # ── Scenario-specific levels ──────────────────────────────────
    s0 = supports[0] if supports else None
    invalidation_note = ""

    if scenario == "TENDANCE_HAUSSIÈRE":
        entry_1 = max(fibs["fib_382"], ma50 * 0.99)
        entry_2 = max(fibs["fib_500"], s0 if s0 else ma50 * 0.95)
        entry_3 = max(fibs["fib_618"], ma200 * 1.01)
        stop_loss = min(fibs["fib_786"], ma200 * 0.97)
        entry_recommended = entry_1
        scenario_note = (
            f"Action en tendance haussière. "
            f"Attendre un pull-back vers la zone Fibonacci 38.2% "
            f"(${entry_1:.2f}) avant d'entrer. "
            f"Ne pas chasser le prix actuel."
        )
        invalidation_note = (
            f"Sortir si clôture sous ${stop_loss:.2f} "
            f"(Fibonacci 78.6% / MA200 — thèse haussière invalidée)."
        )

    elif scenario == "CORRECTION":
        entry_1 = current_price
        entry_2 = max(fibs["fib_618"], s0 if s0 else current_price * 0.95)
        entry_3 = ma200 * 1.01
        stop_loss = ma200 * 0.96
        entry_recommended = entry_1
        scenario_note = (
            f"Action en phase de correction. "
            f"Le prix offre une opportunité d'entrée "
            f"si les fondamentaux sont solides (Score > 60). "
            f"Entrée possible maintenant à ${current_price:.2f} "
            f"avec stop sous MA200."
        )
        invalidation_note = (
            f"Sortir si clôture sous ${stop_loss:.2f} "
            f"(MA200 × 0.96 — correction devient tendance baissière)."
        )

    else:  # SOUS_MA200
        entry_1 = supports[0] if supports else current_price * 0.97
        entry_2 = supports[1] if len(supports) > 1 else current_price * 0.94
        entry_3 = fibs["fib_0"]
        stop_loss = round(fibs["fib_0"] * 0.97, 2)
        entry_recommended = min(entry_1, current_price)
        scenario_note = (
            f"Action sous MA200 (${ma200:.2f}) — contexte technique "
            f"défavorable mais à surveiller. "
            f"Entrée possible sur support à ${entry_1:.2f} "
            f"uniquement si RSI < 40 ET Score fondamental > 60. "
            f"Ne pas entrer au prix actuel sans confirmation."
        )
        invalidation_note = (
            f"Stop loss à ${stop_loss:.2f} "
            f"(sous le plus bas 52 semaines ${fibs['fib_0']:.2f}). "
            f"Si ce niveau est cassé, la thèse est invalidée."
        )

    # ── Take profit levels ────────────────────────────────────────
    if scenario == "TENDANCE_HAUSSIÈRE":
        tp_1 = round(current_price * 1.08, 2)
        tp_2 = fibs["fib_100"]
        tp_3 = round(fibs["fib_100"] * 1.10, 2)
    else:
        tp_1 = round(ma50 * 1.01, 2)
        tp_2 = round(ma200 * 1.02, 2)
        tp_3 = fibs["fib_618"]

    # ── R/R on recommended entry ──────────────────────────────────
    entry_rec_r = round(entry_recommended, 2)
    stop_r = round(stop_loss, 2)
    risk_dist = entry_rec_r - stop_r
    reward_dist = tp_1 - entry_rec_r
    rr = round(reward_dist / risk_dist, 2) if risk_dist > 0 else 0.0

    # ── DCA plan ──────────────────────────────────────────────────
    dca_plan = [
        {
            "tranche": 1,
            "pct_position": 50,
            "prix": round(entry_recommended, 2),
            "condition": f"Prix atteint ${entry_recommended:.2f}",
            "note": "Première position — moitié du sizing prévu",
        },
        {
            "tranche": 2,
            "pct_position": 30,
            "prix": round(entry_2, 2),
            "condition": f"Si correction vers ${entry_2:.2f}",
            "note": "Renforcement sur support — améliore le prix moyen",
        },
        {
            "tranche": 3,
            "pct_position": 20,
            "prix": round(entry_3, 2),
            "condition": f"Rebond confirmé sur ${entry_3:.2f}",
            "note": "Dernier renforcement — niveau de sécurité maximum",
        },
    ]

    return {
        "available": True,
        "scenario": scenario,
        "scenario_note": scenario_note,
        "current_price": current_price,
        "entry_recommended": round(entry_recommended, 2),
        "entry_levels": {
            "entry_1": round(entry_1, 2),
            "entry_2": round(entry_2, 2),
            "entry_3": round(entry_3, 2),
        },
        "stop_loss": stop_r,
        "invalidation_note": invalidation_note,
        "take_profit": {
            "tp_1": tp_1,
            "tp_2": tp_2,
            "tp_3": tp_3,
        },
        "risk_reward": rr,
        "atr": atr,
        "fibonacci": fibs,
        "supports": supports,
        "ma50": ma50,
        "ma200": ma200,
        "dca_plan": dca_plan,
    }
