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
