"""
AAOIFI Shariah Audit — 100% Deterministic Engine.

No AI. No gap-filling. Pure arithmetic on raw API data.

Sources:
  - yfinance: 36-month close prices + sharesOutstanding → avg_market_cap_36m
  - FMP: balance-sheet-statement → debt & investment ratios
  - FMP: income-statement → interest income
  - Musaffa (fallback): revenue segmentation for haram segment detection
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logging import logger
from app.integration import yfinance_client
from app.integration.fmp_client import (
    get_balance_sheet,
    get_income_statement,
    get_revenue_segmentation,
)
from app.integration.musaffa_scraper import scrape_revenue_breakdown
from app.models.schemas import (
    AAOIFIAudit,
    AuditRatio,
    FinancialScreening,
    RevenueScreening,
    RevenueSegment,
    Purification,
)


# ── Helpers ───────────────────────────────────────────────────────

def _safe(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _fmt_large(val: Optional[float]) -> str:
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
    return f"{sign}{abs_val:,.0f}"


def _build_ratio(
    name: str,
    numerator: Optional[float],
    num_label: str,
    denominator: Optional[float],
    den_label: str,
    threshold: float,
) -> AuditRatio:
    if numerator is None or denominator is None or denominator == 0:
        return AuditRatio(
            name=name,
            numerator=numerator,
            numerator_label=num_label,
            denominator=denominator,
            denominator_label=den_label,
            value=None,
            threshold=threshold,
            passed=None,
            display_value="N/A",
            detail=f"Data unavailable: {num_label} or {den_label} is N/A",
        )
    value = numerator / denominator
    passed = value < threshold
    pct = round(value * 100, 2)
    return AuditRatio(
        name=name,
        numerator=numerator,
        numerator_label=num_label,
        denominator=denominator,
        denominator_label=den_label,
        value=round(value, 6),
        threshold=threshold,
        passed=passed,
        display_value=f"{pct}%",
        detail=f"{num_label}/{den_label} = {pct}% ({'<' if passed else '>='} {threshold * 100:.0f}%)",
    )


# ── Step 1: avg_market_cap_36m via yfinance ───────────────────────

async def _compute_avg_market_cap_36m(symbol: str, info: dict) -> Optional[float]:
    """
    Average market cap over 36 months.
    Uses monthly close prices × sharesOutstanding from yfinance.
    """
    shares = _safe(info.get("sharesOutstanding"))
    if shares is None:
        logger.warning("sharesOutstanding unavailable for %s", symbol)
        return None

    try:
        history = await yfinance_client.get_history(symbol, period="3y", interval="1mo")
    except Exception as exc:
        logger.warning("Failed to fetch 3y monthly history for %s: %s", symbol, exc)
        return None

    close = history["Close"].dropna()
    if close.empty:
        return None

    avg_price = float(close.mean())
    avg_mkt_cap = avg_price * shares
    logger.info(
        "avg_market_cap_36m for %s: avg_price=%.2f × shares=%.0f = %.0f",
        symbol, avg_price, shares, avg_mkt_cap,
    )
    return avg_mkt_cap


# ── Step 2: Financial Screening (30%) ────────────────────────────

async def _financial_screening(symbol: str, avg_mkt_cap: Optional[float]) -> tuple[FinancialScreening, Optional[str]]:
    """
    Debt ratio and investments ratio from FMP balance sheet.
    Returns (screening, balance_sheet_date).
    """
    bs_date: Optional[str] = None

    bs = await get_balance_sheet(symbol, limit=1)
    if not bs:
        logger.warning("FMP balance sheet unavailable for %s", symbol)
        debt_ratio = _build_ratio("Debt Ratio", None, "Total Debt", avg_mkt_cap, "Avg MCap 36m", 0.30)
        inv_ratio = _build_ratio("Investments Ratio", None, "Total Investments", avg_mkt_cap, "Avg MCap 36m", 0.30)
        return FinancialScreening(debt_ratio=debt_ratio, investments_ratio=inv_ratio, passed=None), None

    stmt = bs[0]
    bs_date = stmt.get("date")

    logger.info(
        "FMP balance sheet for %s (date=%s): shortTermDebt=%s, longTermDebt=%s, "
        "shortTermInvestments=%s, longTermInvestments=%s",
        symbol, bs_date,
        stmt.get("shortTermDebt"), stmt.get("longTermDebt"),
        stmt.get("shortTermInvestments"), stmt.get("longTermInvestments"),
    )

    short_debt = _safe(stmt.get("shortTermDebt")) or 0
    long_debt = _safe(stmt.get("longTermDebt")) or 0
    total_debt = short_debt + long_debt

    short_inv = _safe(stmt.get("shortTermInvestments")) or 0
    long_inv = _safe(stmt.get("longTermInvestments")) or 0
    total_inv = short_inv + long_inv

    debt_ratio = _build_ratio("Debt Ratio", total_debt, "Total Debt (ST+LT)", avg_mkt_cap, "Avg MCap 36m", 0.30)
    inv_ratio = _build_ratio("Investments Ratio", total_inv, "Total Investments (ST+LT)", avg_mkt_cap, "Avg MCap 36m", 0.30)

    if debt_ratio.passed is None or inv_ratio.passed is None:
        passed = None
    else:
        passed = debt_ratio.passed and inv_ratio.passed

    return FinancialScreening(debt_ratio=debt_ratio, investments_ratio=inv_ratio, passed=passed), bs_date


# ── Step 3: Revenue Screening (5%) ───────────────────────────────

async def _revenue_screening(symbol: str) -> tuple[RevenueScreening, list[str]]:
    """
    Interest income from FMP income statement.
    Haram segments from FMP revenue segmentation or Musaffa fallback.
    """
    flags: list[str] = []

    # 3a. Interest income from FMP
    interest_income: Optional[float] = None
    interest_source = "N/A"
    total_revenue: Optional[float] = None

    inc = await get_income_statement(symbol, limit=1)
    if inc:
        stmt = inc[0]
        logger.info(
            "FMP income statement for %s (date=%s): interestIncome=%s, revenue=%s",
            symbol, stmt.get("date"), stmt.get("interestIncome"), stmt.get("revenue"),
        )
        interest_income = _safe(stmt.get("interestIncome")) or 0
        interest_source = "FMP"
        total_revenue = _safe(stmt.get("revenue"))
    else:
        logger.warning("FMP income statement unavailable for %s", symbol)
        flags.append("INCOME_STMT_UNAVAILABLE")

    # 3b. Revenue segmentation: try FMP first, fallback to Musaffa
    haram_segments: list[RevenueSegment] = []
    haram_from_segments: float = 0
    seg_source = "N/A"

    fmp_seg = await get_revenue_segmentation(symbol)
    if fmp_seg:
        seg_source = "FMP"
        from app.integration.musaffa_scraper import HARAM_KEYWORDS
        for seg_name, seg_val in fmp_seg.items():
            val = _safe(seg_val)
            if val is None or val <= 0:
                continue
            is_haram = any(kw in seg_name.lower() for kw in HARAM_KEYWORDS)
            if is_haram:
                haram_segments.append(RevenueSegment(name=seg_name, revenue=val, is_haram=True))
                haram_from_segments += val
    else:
        # Fallback: Musaffa scraper
        logger.info("FMP segmentation unavailable for %s, trying Musaffa", symbol)
        musaffa = await scrape_revenue_breakdown(symbol)
        if musaffa:
            seg_source = "musaffa"
            for seg_name, seg_val in musaffa.get("haram_segments", {}).items():
                haram_segments.append(RevenueSegment(name=seg_name, revenue=seg_val, is_haram=True))
                haram_from_segments += seg_val
        else:
            seg_source = "N/A"
            flags.append("DATA_INCOMPLETE")

    # 3c. Impure total = interest income + haram segment revenue
    total_impure: Optional[float] = None
    if interest_income is not None:
        total_impure = interest_income + haram_from_segments

    impure_ratio = _build_ratio(
        "Impure Revenue Ratio",
        total_impure,
        "Total Impure (Interest + Haram Segments)",
        total_revenue,
        "Total Revenue",
        0.05,
    )

    if impure_ratio.passed is None:
        passed = None
    else:
        passed = impure_ratio.passed

    screening = RevenueScreening(
        interest_income=interest_income,
        interest_income_source=interest_source,
        haram_segments=haram_segments,
        total_impure=total_impure,
        total_revenue=total_revenue,
        impure_ratio=impure_ratio,
        segmentation_source=seg_source,
        passed=passed,
    )

    return screening, flags


# ── Step 4: Verdict & Purification ────────────────────────────────

def _compute_verdict(
    fin: FinancialScreening,
    rev: RevenueScreening,
    flags: list[str],
) -> tuple[str, str]:
    """
    Deterministic verdict:
    - All pass + no DATA_INCOMPLETE → CONFORME
    - All pass + DATA_INCOMPLETE → À VÉRIFIER (Musaffa data missing)
    - Any fail → NON CONFORME
    - Any None → INCOMPLET
    """
    all_results = [fin.passed, rev.passed]

    if any(r is False for r in all_results):
        return "NON CONFORME", "❌"

    if any(r is None for r in all_results):
        return "INCOMPLET", "🟡"

    if "DATA_INCOMPLETE" in flags:
        return "À VÉRIFIER", "🟡"

    return "CONFORME", "✅"


def _compute_purification(
    total_impure: Optional[float],
    total_revenue: Optional[float],
    dividend_rate: Optional[float],
) -> Purification:
    """Purification = (total_impure / total_revenue) × dividend_per_share."""
    if total_impure is None or total_revenue is None or total_revenue == 0:
        return Purification()

    ratio = total_impure / total_revenue
    if dividend_rate is None or dividend_rate <= 0:
        return Purification(
            impure_ratio=round(ratio, 6),
            dividend_per_share=None,
            purification_per_share=None,
            display_value="N/A (no dividend)",
        )

    purif = round(ratio * dividend_rate, 4)
    return Purification(
        impure_ratio=round(ratio, 6),
        dividend_per_share=dividend_rate,
        purification_per_share=purif,
        display_value=f"{purif:.4f} / action",
    )


# ── Main entry point ─────────────────────────────────────────────

async def run_aaoifi_audit(symbol: str) -> AAOIFIAudit:
    """Execute the full deterministic AAOIFI audit pipeline."""
    logger.info("Starting AAOIFI audit for %s", symbol)

    # Fetch yfinance info for identity + sharesOutstanding + dividendRate
    info = await yfinance_client.get_ticker_info(symbol)

    # Step 1: avg market cap 36m
    avg_mkt_cap = await _compute_avg_market_cap_36m(symbol, info)

    # Step 2: financial screening (parallel with step 3)
    fin_task = _financial_screening(symbol, avg_mkt_cap)
    rev_task = _revenue_screening(symbol)
    (fin_screening, bs_date), (rev_screening, flags) = await asyncio.gather(fin_task, rev_task)

    # Step 4: verdict
    verdict, emoji = _compute_verdict(fin_screening, rev_screening, flags)

    # Step 5: purification
    div_rate = _safe(info.get("dividendRate"))
    purification = _compute_purification(
        rev_screening.total_impure,
        rev_screening.total_revenue,
        div_rate,
    )

    return AAOIFIAudit(
        symbol=symbol,
        company_name=info.get("longName") or info.get("shortName"),
        balance_sheet_date=bs_date,
        avg_market_cap_36m=avg_mkt_cap,
        avg_market_cap_36m_display=_fmt_large(avg_mkt_cap),
        financial_screening=fin_screening,
        revenue_screening=rev_screening,
        purification=purification,
        verdict=verdict,
        verdict_emoji=emoji,
        flags=flags,
        cached_at=datetime.now(timezone.utc).isoformat(),
    )
