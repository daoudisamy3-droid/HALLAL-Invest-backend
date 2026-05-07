"""
Piotroski F-Score — 9-factor financial strength model.

Each computable criterion contributes 1 point (0 or 1); criteria where the
required data is missing are excluded from the denominator rather than
counted as 0.  This avoids penalising companies for data unavailability.

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


def _criterion(condition: Optional[bool]) -> Optional[int]:
    """Return 1/0 if condition is evaluable, None if required data is absent."""
    if condition is None:
        return None
    return 1 if condition else 0


def compute_piotroski(data: dict) -> dict:
    """
    Compute Piotroski F-Score from scoring data dict.

    Returns:
        {
            "score": int,                  # raw points earned
            "score_out_of_9": str,         # "{score}/{n_computable}"
            "normalized": float,           # score / n_computable * 100
            "criteria": {
                "f1_roa_positive": int | None,
                ...
            },
            "n_computable": int,           # criteria with sufficient data
            "available": bool,             # n_computable >= 6
        }
    """
    total_assets: Optional[float] = data.get("total_assets")
    total_assets_prev: Optional[float] = data.get("total_assets_prev")
    net_income: Optional[float] = data.get("net_income")
    net_income_prev: Optional[float] = data.get("net_income_prev")
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

    # ── Derived intermediaries ────────────────────────────────────
    roa: Optional[float] = None
    roa_prev: Optional[float] = None
    if total_assets and total_assets > 0 and net_income is not None:
        roa = net_income / total_assets
    if total_assets_prev and total_assets_prev > 0 and net_income_prev is not None:
        roa_prev = net_income_prev / total_assets_prev

    cfo_over_assets: Optional[float] = None
    if total_assets and total_assets > 0 and operating_cash_flow is not None:
        cfo_over_assets = operating_cash_flow / total_assets

    lev: Optional[float] = None
    lev_prev: Optional[float] = None
    if total_assets and total_assets > 0 and total_debt is not None:
        lev = total_debt / total_assets
    if total_assets_prev and total_assets_prev > 0 and total_debt_prev is not None:
        lev_prev = total_debt_prev / total_assets_prev

    cr: Optional[float] = None
    cr_prev: Optional[float] = None
    if current_liabilities and current_liabilities > 0 and current_assets is not None:
        cr = current_assets / current_liabilities
    if current_liabilities_prev and current_liabilities_prev > 0 and current_assets_prev is not None:
        cr_prev = current_assets_prev / current_liabilities_prev

    gm: Optional[float] = None
    gm_prev: Optional[float] = None
    if revenue and revenue > 0 and gross_profit is not None:
        gm = gross_profit / revenue
    if revenue_prev and revenue_prev > 0 and gross_profit_prev is not None:
        gm_prev = gross_profit_prev / revenue_prev

    at_: Optional[float] = None
    at_prev: Optional[float] = None
    if total_assets and total_assets > 0 and revenue is not None:
        at_ = revenue / total_assets
    if total_assets_prev and total_assets_prev > 0 and revenue_prev is not None:
        at_prev = revenue_prev / total_assets_prev

    # ── Criteria (None = data missing, not counted) ───────────────
    f1 = _criterion(roa > 0 if roa is not None else None)
    f2 = _criterion(operating_cash_flow > 0 if operating_cash_flow is not None else None)
    f3 = _criterion(roa > roa_prev if (roa is not None and roa_prev is not None) else None)
    f4 = _criterion(
        cfo_over_assets > roa
        if (cfo_over_assets is not None and roa is not None)
        else None
    )
    f5 = _criterion(lev < lev_prev if (lev is not None and lev_prev is not None) else None)
    f6 = _criterion(cr > cr_prev if (cr is not None and cr_prev is not None) else None)
    f7 = _criterion(
        shares_outstanding <= shares_outstanding_prev
        if (shares_outstanding is not None and shares_outstanding_prev is not None)
        else None
    )
    f8 = _criterion(gm > gm_prev if (gm is not None and gm_prev is not None) else None)
    f9 = _criterion(at_ > at_prev if (at_ is not None and at_prev is not None) else None)

    criteria_values = [f1, f2, f3, f4, f5, f6, f7, f8, f9]
    computable = [v for v in criteria_values if v is not None]
    n_computable = len(computable)
    raw_score = sum(computable)

    normalized = round(raw_score / n_computable * 100.0, 1) if n_computable > 0 else 50.0
    available = n_computable >= 6

    return {
        "score": raw_score,
        "score_out_of_9": f"{raw_score}/{n_computable}",
        "normalized": normalized,
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
        "n_computable": n_computable,
        "available": available,
    }
