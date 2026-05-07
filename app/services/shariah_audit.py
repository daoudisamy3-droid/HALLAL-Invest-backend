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
from app.integration.yfinance_client import get_interest_income_fallback
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
    source: str = "N/A",
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
            source=source,
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
        source=source,
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
    """Try FMP first, fall back to yfinance (via shariah_service). Returns (data_dict, source)."""

    # Attempt 1: FMP
    fmp_data = await fmp_balance_sheet(symbol, limit=1)
    if fmp_data and len(fmp_data) > 0:
        logger.info("Balance sheet source: FMP for %s", symbol)
        return fmp_data[0], "FMP"

    # Attempt 2: yfinance (standard client)
    logger.info("FMP balance sheet failed for %s — falling back to yfinance", symbol)
    yf_data = await yfinance_client.get_balance_sheet(symbol)
    if yf_data:
        logger.info("Balance sheet source: yfinance for %s (keys: %s)", symbol, list(yf_data.keys())[:10])
        return yf_data, "yfinance"

    # Attempt 3: shariah_service (quarterly balance sheet + adjusted close)
    logger.info("yfinance annual failed for %s — trying shariah_service (quarterly)", symbol)
    try:
        from app.services.shariah_service import get_audit_data
        svc_data = await get_audit_data(symbol)
        if svc_data.get("total_assets") is not None or svc_data.get("total_debt") is not None:
            logger.info("Balance sheet source: shariah_service for %s", symbol)
            return svc_data, "yfinance"
    except Exception as exc:
        logger.warning("shariah_service fallback failed for %s: %s", symbol, exc)

    logger.warning("Balance sheet unavailable from all sources for %s", symbol)
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


def _extract_total_assets(stmt: dict, source: str) -> Optional[float]:
    """Extract total assets from a statement dict."""
    if source == "FMP":
        return _safe(stmt.get("totalAssets"))
    # yfinance
    return _safe(stmt.get("Total Assets"))


async def _financial_screening(symbol: str, avg_mkt_cap: Optional[float]) -> tuple[FinancialScreening, Optional[str], str]:
    """Returns (screening, balance_sheet_date, source)."""
    stmt, source = await _fetch_balance_sheet_data(symbol)

    if not stmt:
        debt_ratio = _build_ratio("Debt Ratio", None, "Total Debt", avg_mkt_cap, "Avg MCap 36m", 0.30, source="N/A")
        inv_ratio = _build_ratio("Investments Ratio", None, "Total Investments", avg_mkt_cap, "Avg MCap 36m", 0.30, source="N/A")
        return FinancialScreening(debt_ratio=debt_ratio, investments_ratio=inv_ratio, passed=None, source="N/A"), None, "N/A"

    bs_date = stmt.get("date") or stmt.get("Date") or None

    total_debt = _extract_debt(stmt, source)
    total_inv = _extract_investments(stmt, source)

    # ── Primary: Debt/Assets ratio (OpenBB or any source with total_assets)
    total_assets = _extract_total_assets(stmt, source)

    if total_assets and total_assets > 0:
        # Use Debt/Total Assets as the primary debt ratio (AAOIFI threshold: 30%)
        logger.info(
            "Using Debt/Assets ratio for %s: debt=%.0f / assets=%.0f (source=%s)",
            symbol, total_debt, total_assets, source,
        )
        debt_ratio = _build_ratio(
            "Debt Ratio", total_debt, "Total Debt",
            total_assets, "Total Assets", 0.30, source=source,
        )
    else:
        # Fallback: Debt / Avg Market Cap 36m
        debt_ratio = _build_ratio(
            "Debt Ratio", total_debt, "Total Debt (ST+LT)",
            avg_mkt_cap, "Avg MCap 36m", 0.30, source=source,
        )

    inv_ratio = _build_ratio("Investments Ratio", total_inv, "Total Investments (ST+LT)", avg_mkt_cap, "Avg MCap 36m", 0.30, source=source)

    if debt_ratio.passed is None or inv_ratio.passed is None:
        passed = None
    else:
        passed = debt_ratio.passed and inv_ratio.passed

    return FinancialScreening(debt_ratio=debt_ratio, investments_ratio=inv_ratio, passed=passed, source=source), bs_date, source


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


def _extract_interest_income(stmt: dict, source: str) -> Optional[float]:
    """
    Extract interest income. Returns None if the field doesn't exist in the
    statement — never defaults to 0 when data is truly absent.
    """
    if source == "FMP":
        val = _safe(stmt.get("interestIncome"))
    else:
        val = _safe(stmt.get("Interest Income"))

    if val is None:
        logger.warning("Interest income field NOT FOUND in %s statement", source)
        return None

    result = abs(val)
    logger.info("Interest income extraction (%s): %.0f", source, result)
    return result


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

        # Per-field fallback: if FMP returned the statement but interestIncome
        # is 0 or None, try yfinance specifically for this field.
        if (interest_income is None or interest_income == 0) and inc_source == "FMP":
            logger.info(
                "FMP interestIncome is %s for %s — trying yfinance fallback",
                interest_income, symbol,
            )
            yf_interest = await get_interest_income_fallback(symbol)
            if yf_interest is not None and yf_interest > 0:
                interest_income = yf_interest
                interest_source = "yfinance"
                logger.info(
                    "Interest income recovered via yfinance for %s: %.0f",
                    symbol, interest_income,
                )

        logger.info(
            "Revenue data for %s (%s): interest=%s, total_revenue=%s",
            symbol, interest_source, interest_income, total_revenue,
        )
        # If the interest income field was missing from all sources, flag it
        if interest_income is None:
            flags.append("INTEREST_INCOME_UNAVAILABLE")
    else:
        logger.warning("Income statement unavailable for %s from all sources", symbol)
        flags.append("INCOME_STMT_UNAVAILABLE")

    # 3b. Revenue segmentation: FMP → Musaffa fallback
    all_segments: list[RevenueSegment] = []
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
            segment = RevenueSegment(name=seg_name, revenue=val, is_haram=is_haram)
            all_segments.append(segment)
            if is_haram:
                haram_segments.append(segment)
                haram_from_segments += val

    if not fmp_seg:
        # FMP segmentation unavailable → try Musaffa scraper
        logger.info("FMP segmentation unavailable for %s, trying Musaffa", symbol)
        musaffa = await scrape_revenue_breakdown(symbol)
        if musaffa:
            seg_source = "musaffa"
            # Include ALL segments from Musaffa for full visibility
            for seg_name, seg_val in musaffa.get("segments", {}).items():
                is_haram = any(kw in seg_name.lower() for kw in HARAM_KEYWORDS)
                segment = RevenueSegment(name=seg_name, revenue=seg_val, is_haram=is_haram)
                all_segments.append(segment)
                if is_haram:
                    haram_segments.append(segment)
                    haram_from_segments += seg_val
        else:
            seg_source = "N/A"
            flags.append("SEGMENTATION_UNAVAILABLE")

    # 3c. Impure total — only compute if we have at least interest OR segments
    total_impure: Optional[float] = None
    if interest_income is not None:
        total_impure = interest_income + haram_from_segments
    elif haram_from_segments > 0:
        # We have segment data but no interest income — partial
        total_impure = haram_from_segments

    # Source for the impure ratio = combination of income + segmentation sources
    impure_sources = []
    if interest_source != "N/A":
        impure_sources.append(interest_source)
    if seg_source != "N/A" and seg_source not in impure_sources:
        impure_sources.append(seg_source)
    impure_source_str = "+".join(impure_sources) if impure_sources else "N/A"

    impure_ratio = _build_ratio(
        "Impure Revenue Ratio",
        total_impure,
        "Total Impure (Interest + Haram Segments)",
        total_revenue,
        "Total Revenue",
        0.05,
        source=impure_source_str,
    )

    passed = impure_ratio.passed

    screening = RevenueScreening(
        interest_income=interest_income,
        interest_income_source=interest_source,
        all_segments=all_segments,
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
) -> tuple[str, str, list[str]]:
    """
    Returns (verdict, emoji, updated_flags).

    Key rule: if interest_income is 0 or None AND no haram segments detected,
    the data is insufficient to conclude compliance — never say CONFORME.
    """
    all_results = [fin.passed, rev.passed]

    if any(r is False for r in all_results):
        return "NON CONFORME", "❌", flags

    if any(r is None for r in all_results):
        return "INCOMPLET", "🟡", flags

    # Suspicion check: zero impure with no segmentation = incomplete data
    interest = rev.interest_income
    has_segments = len(rev.haram_segments) > 0
    has_segmentation_source = rev.segmentation_source != "N/A"

    if (interest is None or interest == 0) and not has_segments and not has_segmentation_source:
        if "DATA_INCOMPLETE" not in flags:
            flags.append("DATA_INCOMPLETE")
        logger.warning(
            "Verdict guardrail: interest=%s, segments=%d, seg_source=%s → cannot confirm compliance",
            interest, len(rev.haram_segments), rev.segmentation_source,
        )
        return "À VÉRIFIER", "🟡", flags

    if any(f in flags for f in ("DATA_INCOMPLETE", "INTEREST_INCOME_UNAVAILABLE", "SEGMENTATION_UNAVAILABLE")):
        return "À VÉRIFIER", "🟡", flags

    return "CONFORME", "✅", flags


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

    # Step 4: verdict (may update flags)
    verdict, emoji, flags = _compute_verdict(fin_screening, rev_screening, flags)

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
