"""
AAOIFI Shariah Audit — 100% Deterministic Engine.

No AI. No gap-filling. Pure arithmetic on raw API data.

Data cascade:
  1. FMP (primary)   → balance-sheet-statement, income-statement
  2. yfinance (fallback) → ticker.balance_sheet, ticker.income_stmt
  3. Musaffa (fallback)  → revenue segmentation scraping
"""

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from app.core.logging import logger
from app.integration import yfinance_client
from app.integration.fmp_client import (
    get_balance_sheet as fmp_balance_sheet,
    get_income_statement as fmp_income_statement,
    get_revenue_segmentation as fmp_revenue_segmentation,
)
from app.integration.musaffa_scraper import scrape_revenue_breakdown, HARAM_KEYWORDS
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


# ── Step 2: Financial Screening (30%) with yfinance fallback ─────

async def _fetch_balance_sheet_data(symbol: str) -> tuple[Optional[dict], str]:
    """Try FMP first, fall back to yfinance. Returns (data_dict, source)."""

    # Attempt 1: FMP
    fmp_data = await fmp_balance_sheet(symbol, limit=1)
    if fmp_data and len(fmp_data) > 0:
        logger.info("Balance sheet source: FMP for %s", symbol)
        return fmp_data[0], "FMP"

    # Attempt 2: yfinance
    logger.info("FMP balance sheet failed for %s — falling back to yfinance", symbol)
    yf_data = await yfinance_client.get_balance_sheet(symbol)
    if yf_data:
        logger.info("Balance sheet source: yfinance for %s (keys: %s)", symbol, list(yf_data.keys())[:10])
        return yf_data, "yfinance"

    logger.warning("Balance sheet unavailable from both FMP and yfinance for %s", symbol)
    return None, "N/A"


def _extract_debt(stmt: dict, source: str) -> float:
    """Extract total debt from a statement dict, adapting to FMP or yfinance field names."""
    if source == "FMP":
        short = _safe(stmt.get("shortTermDebt")) or 0
        long = _safe(stmt.get("longTermDebt")) or 0
    else:
        # yfinance uses different field names
        short = _safe(stmt.get("Current Debt")) or _safe(stmt.get("Short Term Debt")) or 0
        long = _safe(stmt.get("Long Term Debt")) or _safe(stmt.get("Long Term Debt And Capital Lease Obligation")) or 0
    total = short + long
    logger.info("Debt extraction (%s): short=%.0f + long=%.0f = %.0f", source, short, long, total)
    return total


def _extract_investments(stmt: dict, source: str) -> float:
    """Extract total investments from a statement dict."""
    if source == "FMP":
        short = _safe(stmt.get("shortTermInvestments")) or 0
        long = _safe(stmt.get("longTermInvestments")) or 0
    else:
        short = _safe(stmt.get("Other Short Term Investments")) or _safe(stmt.get("Short Term Investments")) or 0
        long = _safe(stmt.get("Long Term Equity Investment")) or _safe(stmt.get("Investments And Advances")) or 0
    total = short + long
    logger.info("Investments extraction (%s): short=%.0f + long=%.0f = %.0f", source, short, long, total)
    return total


async def _financial_screening(symbol: str, avg_mkt_cap: Optional[float]) -> tuple[FinancialScreening, Optional[str], str]:
    """Returns (screening, balance_sheet_date, source)."""
    stmt, source = await _fetch_balance_sheet_data(symbol)

    if not stmt:
        debt_ratio = _build_ratio("Debt Ratio", None, "Total Debt", avg_mkt_cap, "Avg MCap 36m", 0.30)
        inv_ratio = _build_ratio("Investments Ratio", None, "Total Investments", avg_mkt_cap, "Avg MCap 36m", 0.30)
        return FinancialScreening(debt_ratio=debt_ratio, investments_ratio=inv_ratio, passed=None), None, "N/A"

    bs_date = stmt.get("date") or stmt.get("Date") or None

    total_debt = _extract_debt(stmt, source)
    total_inv = _extract_investments(stmt, source)

    debt_ratio = _build_ratio("Debt Ratio", total_debt, f"Total Debt ({source})", avg_mkt_cap, "Avg MCap 36m", 0.30)
    inv_ratio = _build_ratio("Investments Ratio", total_inv, f"Total Investments ({source})", avg_mkt_cap, "Avg MCap 36m", 0.30)

    if debt_ratio.passed is None or inv_ratio.passed is None:
        passed = None
    else:
        passed = debt_ratio.passed and inv_ratio.passed

    return FinancialScreening(debt_ratio=debt_ratio, investments_ratio=inv_ratio, passed=passed), bs_date, source


# ── Step 3: Revenue Screening (5%) with yfinance fallback ────────

async def _fetch_income_data(symbol: str) -> tuple[Optional[dict], str]:
    """Try FMP first, fall back to yfinance. Returns (data_dict, source)."""

    # Attempt 1: FMP
    fmp_data = await fmp_income_statement(symbol, limit=1)
    if fmp_data and len(fmp_data) > 0:
        logger.info("Income statement source: FMP for %s", symbol)
        return fmp_data[0], "FMP"

    # Attempt 2: yfinance
    logger.info("FMP income statement failed for %s — falling back to yfinance", symbol)
    yf_data = await yfinance_client.get_income_stmt(symbol)
    if yf_data:
        logger.info("Income statement source: yfinance for %s (keys: %s)", symbol, list(yf_data.keys())[:10])
        return yf_data, "yfinance"

    logger.warning("Income statement unavailable from both FMP and yfinance for %s", symbol)
    return None, "N/A"


def _extract_interest_income(stmt: dict, source: str) -> float:
    """Extract interest income, adapting to FMP or yfinance field names."""
    if source == "FMP":
        val = _safe(stmt.get("interestIncome")) or _safe(stmt.get("interestExpense")) or 0
    else:
        val = _safe(stmt.get("Interest Income")) or _safe(stmt.get("Interest Expense")) or 0
    logger.info("Interest income extraction (%s): %.0f", source, abs(val))
    return abs(val)


def _extract_total_revenue(stmt: dict, source: str) -> Optional[float]:
    """Extract total revenue."""
    if source == "FMP":
        return _safe(stmt.get("revenue"))
    return _safe(stmt.get("Total Revenue"))


async def _revenue_screening(symbol: str) -> tuple[RevenueScreening, list[str]]:
    flags: list[str] = []

    # 3a. Interest income + total revenue
    stmt, inc_source = await _fetch_income_data(symbol)

    interest_income: Optional[float] = None
    interest_source = "N/A"
    total_revenue: Optional[float] = None

    if stmt:
        interest_income = _extract_interest_income(stmt, inc_source)
        interest_source = inc_source
        total_revenue = _extract_total_revenue(stmt, inc_source)
        logger.info(
            "Revenue data for %s (%s): interest=%.0f, total_revenue=%s",
            symbol, inc_source, interest_income, total_revenue,
        )
    else:
        logger.warning("Income statement unavailable for %s from all sources", symbol)
        flags.append("INCOME_STMT_UNAVAILABLE")

    # 3b. Revenue segmentation: FMP → Musaffa fallback
    haram_segments: list[RevenueSegment] = []
    haram_from_segments: float = 0
    seg_source = "N/A"

    fmp_seg = await fmp_revenue_segmentation(symbol)
    if fmp_seg:
        seg_source = "FMP"
        for seg_name, seg_val in fmp_seg.items():
            val = _safe(seg_val)
            if val is None or val <= 0:
                continue
            is_haram = any(kw in seg_name.lower() for kw in HARAM_KEYWORDS)
            if is_haram:
                haram_segments.append(RevenueSegment(name=seg_name, revenue=val, is_haram=True))
                haram_from_segments += val
    else:
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

    # 3c. Impure total
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
    logger.info("=== AAOIFI AUDIT START for %s ===", symbol)

    # Fetch yfinance info for identity + sharesOutstanding + dividendRate
    info = await yfinance_client.get_ticker_info(symbol)

    # Step 1: avg market cap 36m
    avg_mkt_cap = await _compute_avg_market_cap_36m(symbol, info)

    # Step 2 + 3: parallel
    fin_task = _financial_screening(symbol, avg_mkt_cap)
    rev_task = _revenue_screening(symbol)
    (fin_screening, bs_date, fin_source), (rev_screening, flags) = await asyncio.gather(fin_task, rev_task)

    # Step 4: verdict
    verdict, emoji = _compute_verdict(fin_screening, rev_screening, flags)

    # Step 5: purification
    div_rate = _safe(info.get("dividendRate"))
    purification = _compute_purification(
        rev_screening.total_impure,
        rev_screening.total_revenue,
        div_rate,
    )

    logger.info(
        "=== AAOIFI AUDIT END for %s: verdict=%s fin_source=%s rev_source=%s flags=%s ===",
        symbol, verdict, fin_source, rev_screening.interest_income_source, flags,
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
