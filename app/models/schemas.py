from pydantic import BaseModel, Field
from typing import Optional


# ── Shariah Screening (pure ratio-based, no AI) ──────────────────

class ShariahRatio(BaseModel):
    name: str
    value: Optional[float] = None
    threshold: float
    passed: Optional[bool] = None
    detail: str


class ShariahLevel(BaseModel):
    level_name: str = Field(description="AAOIFI or Strict")
    passed: Optional[bool] = None
    ratios: list[ShariahRatio]


class ShariahScreening(BaseModel):
    halal_badge: str = Field(description="PASS, FAIL, DOUBTFUL, or INCONCLUSIVE")
    aaoifi: ShariahLevel
    strict: ShariahLevel
    summary: str


# ── Fundamentals (raw native fields from yfinance) ───────────────

class Fundamentals(BaseModel):
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    currency: Optional[str] = None
    website: Optional[str] = None
    market_cap: Optional[float] = None
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    peg_ratio: Optional[float] = None
    dividend_yield: Optional[float] = None
    return_on_equity: Optional[float] = None
    gross_margins: Optional[float] = None
    debt_to_equity: Optional[float] = None
    total_debt: Optional[float] = None
    total_revenue: Optional[float] = None
    free_cashflow: Optional[float] = None


# ── Technicals (computed from raw OHLC data, no AI) ──────────────

class BollingerBands(BaseModel):
    upper: Optional[float] = None
    middle: Optional[float] = None
    lower: Optional[float] = None


class Technicals(BaseModel):
    current_price: Optional[float] = None
    rsi_14: Optional[float] = None
    sma_50: Optional[float] = None
    sma_200: Optional[float] = None
    bollinger_bands: Optional[BollingerBands] = None
    max_drawdown_5y: Optional[float] = None


# ── AI Commentary (read-only analysis, never modifies data) ──────

class AISection(BaseModel):
    rating: Optional[str] = None
    summary: str
    details: list[str] = []


class EntryZone(BaseModel):
    low: Optional[float] = None
    high: Optional[float] = None


class AICommentary(BaseModel):
    shariah_compliance: Optional[AISection] = None
    company_quality: Optional[AISection] = None
    valuation: Optional[AISection] = None
    entry_timing: Optional[AISection] = None
    final_verdict: Optional[str] = Field(None, description="BUY / WAIT / AVOID")
    entry_zone: Optional[EntryZone] = None
    stop_loss: Optional[float] = None
    target_18m: Optional[float] = None
    raw_response: Optional[str] = Field(None, description="Raw Claude JSON if parsing fails")


# ── Lightweight Price ─────────────────────────────────────────────

class PriceResponse(BaseModel):
    current_price: float
    change: float
    change_pct: float


# ── Aggregated Ticker Response ────────────────────────────────────

class TickerResponse(BaseModel):
    symbol: str
    fundamentals: Fundamentals
    technicals: Technicals
    shariah: ShariahScreening
    ai_commentary: Optional[AICommentary] = None
    errors: list[str] = Field(default_factory=list, description="Non-fatal warnings")
