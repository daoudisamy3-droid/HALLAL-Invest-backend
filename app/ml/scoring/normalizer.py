"""
Normalizer — maps raw metric values to 0-100 scores.

All functions are pure (no I/O) and return float in [0, 100].
None inputs always return 50.0 (neutral / unknown).
"""

from typing import Optional


def _clamp(val: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, val))


def normalize_linear(
    val: Optional[float],
    lo: float,
    hi: float,
    reverse: bool = False,
) -> float:
    """Linear interpolation between lo→0 and hi→100 (or reversed)."""
    if val is None:
        return 50.0
    if hi == lo:
        return 50.0
    score = (val - lo) / (hi - lo) * 100.0
    if reverse:
        score = 100.0 - score
    return _clamp(score)


def normalize_piotroski(score: Optional[int]) -> float:
    """0-9 integer → 0-100 linearly."""
    if score is None:
        return 50.0
    return _clamp(score / 9.0 * 100.0)


def normalize_altman(z: Optional[float]) -> float:
    """
    Altman Z-score → 0-100.

    Z ≥ 2.99 → SÛRE     → ~90-100
    1.81 ≤ Z < 2.99 → GRISE  → ~40-89
    Z < 1.81 → DANGEREUSE → 0-39
    """
    if z is None:
        return 50.0
    if z >= 3.0:
        return _clamp(normalize_linear(z, 3.0, 6.0) * 0.1 + 90.0)
    if z >= 1.81:
        return _clamp(normalize_linear(z, 1.81, 3.0) * 0.5 + 40.0)
    return _clamp(normalize_linear(z, -2.0, 1.81) * 0.4)


def normalize_margin_of_safety(mos_pct: Optional[float]) -> float:
    """
    Margin of safety = (graham_number - price) / graham_number * 100.

    MOS > 30% → excellent → 100
    MOS 0-30% → good → 50-99
    MOS negative → overvalued → 0-49
    """
    if mos_pct is None:
        return 50.0
    if mos_pct >= 30.0:
        return 100.0
    if mos_pct >= 0.0:
        return _clamp(50.0 + mos_pct / 30.0 * 50.0)
    return _clamp(50.0 + mos_pct / 50.0 * 50.0)


def normalize_peg(peg: Optional[float]) -> float:
    """
    PEG < 1 → undervalued → 100
    PEG 1-2 → fair → 50-99
    PEG > 2 → overvalued → 0-49
    Negative PEG or None → 50 (neutral / uninterpretable)
    """
    if peg is None or peg <= 0:
        return 50.0
    if peg < 1.0:
        return _clamp(100.0 - peg * 50.0)
    if peg <= 2.0:
        return _clamp(100.0 - (peg - 1.0) * 50.0)
    return _clamp(normalize_linear(peg, 2.0, 5.0, reverse=True) * 0.5)


def normalize_rsi(rsi: Optional[float]) -> float:
    """
    RSI 40-60 → neutral → ~50
    RSI 30-40 → mildly oversold → 60-70 (good entry)
    RSI < 30  → oversold → 80-100 (strong entry)
    RSI 60-70 → mildly overbought → 30-49
    RSI > 70  → overbought → 0-29
    """
    if rsi is None:
        return 50.0
    if rsi < 30:
        return _clamp(80.0 + (30.0 - rsi) / 30.0 * 20.0)
    if rsi < 40:
        return _clamp(60.0 + (40.0 - rsi) / 10.0 * 20.0)
    if rsi <= 60:
        return 50.0
    if rsi <= 70:
        return _clamp(30.0 + (70.0 - rsi) / 10.0 * 20.0)
    return _clamp(normalize_linear(rsi, 70.0, 100.0, reverse=True) * 0.3)


def normalize_cagr(cagr_pct: Optional[float]) -> float:
    """
    CAGR % → 0-100.

    > 20% → excellent
    10-20% → good
    0-10% → acceptable
    < 0% → bad
    """
    if cagr_pct is None:
        return 50.0
    if cagr_pct >= 20.0:
        return _clamp(80.0 + min(cagr_pct - 20.0, 20.0))
    if cagr_pct >= 10.0:
        return _clamp(60.0 + (cagr_pct - 10.0) * 2.0)
    if cagr_pct >= 0.0:
        return _clamp(40.0 + cagr_pct * 2.0)
    return _clamp(40.0 + cagr_pct * 1.0)


def normalize_beat_rate(beats: Optional[int], total: Optional[int]) -> float:
    """
    EPS beat rate (out of 4 quarters).

    4/4 → 100, 3/4 → 75, 2/4 → 50, 1/4 → 25, 0/4 → 0
    0 total → 50 (no data)
    """
    if not total or total == 0:
        return 50.0
    rate = (beats or 0) / total
    return _clamp(rate * 100.0)


def normalize_fcf_quality(fcf_quality: Optional[float]) -> float:
    """
    FCF quality = operating_cash_flow / net_income.

    > 1.2 → excellent cash conversion → 100
    0.8-1.2 → good → 70-99
    0.5-0.8 → fair → 40-69
    < 0.5 → poor → 0-39
    """
    if fcf_quality is None:
        return 50.0
    if fcf_quality >= 1.2:
        return _clamp(90.0 + min((fcf_quality - 1.2) * 10.0, 10.0))
    if fcf_quality >= 0.8:
        return _clamp(70.0 + (fcf_quality - 0.8) / 0.4 * 20.0)
    if fcf_quality >= 0.5:
        return _clamp(40.0 + (fcf_quality - 0.5) / 0.3 * 30.0)
    if fcf_quality >= 0.0:
        return _clamp(fcf_quality / 0.5 * 40.0)
    return 50.0  # NI < 0 with positive CFO = ambiguous, not clearly negative


def normalize_position_52w(pos: Optional[float]) -> float:
    """
    52-week position = (price - 52w_low) / (52w_high - 52w_low).

    0.0 → at 52w low → 70 (deeply discounted, contrarian value signal)
    0.5 → midrange → 50
    1.0 → at 52w high → 30 (momentum concern for value investors)
    """
    if pos is None:
        return 50.0
    return _clamp(70.0 - pos * 40.0)
