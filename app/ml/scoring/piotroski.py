"""
Piotroski F-Score — 9-factor financial strength model.

Each criterion contributes 1 point (0 or 1).
Score 0-2: weak, 3-6: average, 7-9: strong.

Profitability (F1-F4):
  F1: ROA > 0
  F2: Operating cash flow > 0
  F3: ROA improved YoY
  F4: Accrual quality (CFO/assets > ROA → cash earnings exceed accrual earnings)

Leverage & Liquidity (F5-F7):
  F5: Long-term debt ratio decreased YoY
  F6: Current ratio improved YoY
  F7: No new shares issued YoY

Operating Efficiency (F8-F9):
  F8: Gross margin improved YoY
  F9: Asset turnover improved YoY
"""

from typing import Optional


def _f(condition: Optional[bool]) -> int:
    """Return 1 if condition is True, else 0. None → 0."""
    return 1 if condition is True else 0


def compute_piotroski(data: dict) -> dict:
    """
    Compute Piotroski F-Score from scoring data dict.

    Returns:
        {
            "score": int (0-9),
            "normalized": float (0-100),
            "criteria": {
                "f1_roa_positive": int,
                "f2_cfo_positive": int,
                "f3_roa_improved": int,
                "f4_accrual_quality": int,
                "f5_leverage_decreased": int,
                "f6_liquidity_improved": int,
                "f7_no_dilution": int,
                "f8_gross_margin_improved": int,
                "f9_asset_turnover_improved": int,
            },
            "available": bool,
        }
    """
    total_assets: Optional[float] = data.get("total_assets")
    total_assets_prev: Optional[float] = data.get("total_assets_prev")
    net_income: Optional[float] = data.get("net_income")
    operating_cash_flow: Optional[float] = data.get("operating_cash_flow")
    total_debt: Optional[float] = data.get("total_debt")
    total_debt_prev: Optional[float] = data.get("total_debt_prev")
    current_assets: Optional[float] = data.get("current_assets")
    current_assets_prev: Optional[float] = data.get("current_assets_prev")
    current_liabilities: Optional[float] = data.get("current_liabilities")
    current_liabilities_prev: Optional[float] = data.get("current_liabilities_prev")
    shares_outstanding: Optional[float] = data.get("shares_outstanding")
    shares_outstanding_prev: Optional[float] = data.get("shares_outstanding_prev")
    gross_profit: Optional[float] = data.get("gross_profit")
    gross_profit_prev: Optional[float] = data.get("gross_profit_prev")
    revenue: Optional[float] = data.get("revenue")
    revenue_prev: Optional[float] = data.get("revenue_prev")

    available = (
        total_assets is not None
        and net_income is not None
        and operating_cash_flow is not None
    )

    # ── F1: ROA positive ─────────────────────────────────────────
    roa: Optional[float] = None
    roa_prev: Optional[float] = None
    if total_assets and total_assets > 0 and net_income is not None:
        roa = net_income / total_assets
    if total_assets_prev and total_assets_prev > 0 and data.get("net_income_prev") is not None:
        roa_prev = data["net_income_prev"] / total_assets_prev

    f1 = _f(roa is not None and roa > 0)

    # ── F2: CFO positive ─────────────────────────────────────────
    f2 = _f(operating_cash_flow is not None and operating_cash_flow > 0)

    # ── F3: ROA improved ─────────────────────────────────────────
    f3 = _f(roa is not None and roa_prev is not None and roa > roa_prev)

    # ── F4: Accrual quality (CFO/assets > ROA) ───────────────────
    cfo_over_assets: Optional[float] = None
    if total_assets and total_assets > 0 and operating_cash_flow is not None:
        cfo_over_assets = operating_cash_flow / total_assets
    f4 = _f(cfo_over_assets is not None and roa is not None and cfo_over_assets > roa)

    # ── F5: Leverage decreased ────────────────────────────────────
    lev: Optional[float] = None
    lev_prev: Optional[float] = None
    if total_assets and total_assets > 0 and total_debt is not None:
        lev = total_debt / total_assets
    if total_assets_prev and total_assets_prev > 0 and total_debt_prev is not None:
        lev_prev = total_debt_prev / total_assets_prev
    f5 = _f(lev is not None and lev_prev is not None and lev < lev_prev)

    # ── F6: Current ratio improved ────────────────────────────────
    cr: Optional[float] = None
    cr_prev: Optional[float] = None
    if current_liabilities and current_liabilities > 0 and current_assets is not None:
        cr = current_assets / current_liabilities
    if current_liabilities_prev and current_liabilities_prev > 0 and current_assets_prev is not None:
        cr_prev = current_assets_prev / current_liabilities_prev
    f6 = _f(cr is not None and cr_prev is not None and cr > cr_prev)

    # ── F7: No dilution ───────────────────────────────────────────
    f7 = _f(
        shares_outstanding is not None
        and shares_outstanding_prev is not None
        and shares_outstanding <= shares_outstanding_prev
    )

    # ── F8: Gross margin improved ─────────────────────────────────
    gm: Optional[float] = None
    gm_prev: Optional[float] = None
    if revenue and revenue > 0 and gross_profit is not None:
        gm = gross_profit / revenue
    if revenue_prev and revenue_prev > 0 and gross_profit_prev is not None:
        gm_prev = gross_profit_prev / revenue_prev
    f8 = _f(gm is not None and gm_prev is not None and gm > gm_prev)

    # ── F9: Asset turnover improved ───────────────────────────────
    at_: Optional[float] = None
    at_prev: Optional[float] = None
    if total_assets and total_assets > 0 and revenue is not None:
        at_ = revenue / total_assets
    if total_assets_prev and total_assets_prev > 0 and revenue_prev is not None:
        at_prev = revenue_prev / total_assets_prev
    f9 = _f(at_ is not None and at_prev is not None and at_ > at_prev)

    score = f1 + f2 + f3 + f4 + f5 + f6 + f7 + f8 + f9

    return {
        "score": score,
        "normalized": round(score / 9.0 * 100.0, 1),
        "criteria": {
            "f1_roa_positive": f1,
            "f2_cfo_positive": f2,
            "f3_roa_improved": f3,
            "f4_accrual_quality": f4,
            "f5_leverage_decreased": f5,
            "f6_liquidity_improved": f6,
            "f7_no_dilution": f7,
            "f8_gross_margin_improved": f8,
            "f9_asset_turnover_improved": f9,
        },
        "available": available,
    }
