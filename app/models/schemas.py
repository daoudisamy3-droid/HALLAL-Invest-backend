from pydantic import BaseModel
from typing import Optional


class Metric(BaseModel):
    """Single data point with provenance tracking for the frontend."""
    value: Optional[float] = None
    display_value: str = "N/A"
    source: str = "yfinance"


class DataGeneral(BaseModel):
    """Full ticker response: identity + fundamentals + technicals + advanced indicators."""

    # ── Identity ──────────────────────────────────────────────────
    symbol: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    currency: Optional[str] = None
    website: Optional[str] = None

    # ── Fondamentaux (natifs yfinance) ────────────────────────────
    market_cap: Metric
    trailing_pe: Metric
    price_to_sales: Metric
    profit_margins: Metric
    return_on_equity: Metric
    return_on_assets: Metric
    revenue_per_share: Metric
    dividend_rate: Metric

    # ── Données Techniques (natifs yfinance) ──────────────────────
    current_price: Metric
    regular_market_change_pct: Metric
    fifty_two_week_high: Metric
    fifty_two_week_low: Metric
    fifty_day_average: Metric
    two_hundred_day_average: Metric

    # ── Indicateurs Avancés (calculés depuis historique) ──────────
    rsi_14: Metric
    max_drawdown_5y: Metric


class PriceResponse(BaseModel):
    """Lightweight price snapshot."""
    current_price: float
    change: float
    change_pct: float


# ── AAOIFI Audit ──────────────────────────────────────────────────

class AuditRatio(BaseModel):
    """Single AAOIFI screening ratio with full traceability."""
    name: str
    numerator: Optional[float] = None
    numerator_label: str
    denominator: Optional[float] = None
    denominator_label: str
    value: Optional[float] = None
    threshold: float
    passed: Optional[bool] = None
    display_value: str = "N/A"
    detail: str = ""


class FinancialScreening(BaseModel):
    """Bloc Financier (seuil 30%)."""
    debt_ratio: AuditRatio
    investments_ratio: AuditRatio
    passed: Optional[bool] = None


class RevenueSegment(BaseModel):
    """Single revenue segment with haram flag."""
    name: str
    revenue: float
    is_haram: bool


class RevenueScreening(BaseModel):
    """Bloc Activités / Revenus (seuil 5%)."""
    interest_income: Optional[float] = None
    interest_income_source: str = "N/A"
    haram_segments: list[RevenueSegment] = []
    total_impure: Optional[float] = None
    total_revenue: Optional[float] = None
    impure_ratio: AuditRatio
    segmentation_source: str = "N/A"
    passed: Optional[bool] = None


class Purification(BaseModel):
    """Purification amount per share."""
    impure_ratio: Optional[float] = None
    dividend_per_share: Optional[float] = None
    purification_per_share: Optional[float] = None
    display_value: str = "N/A"


class AAOIFIAudit(BaseModel):
    """Complete AAOIFI audit response."""
    symbol: str
    company_name: Optional[str] = None
    balance_sheet_date: Optional[str] = None
    avg_market_cap_36m: Optional[float] = None
    avg_market_cap_36m_display: str = "N/A"

    financial_screening: FinancialScreening
    revenue_screening: RevenueScreening
    purification: Purification

    verdict: str
    verdict_emoji: str
    flags: list[str] = []
    cached_at: Optional[str] = None
