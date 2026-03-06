from pydantic import BaseModel, Field
from typing import Optional


# ── Shariah Screening ─────────────────────────────────────────────

class ShariahRatio(BaseModel):
    name: str
    value: Optional[float] = None
    threshold: float
    passed: bool
    detail: str


class ShariahLevel(BaseModel):
    level_name: str = Field(description="AAOIFI or Strict")
    passed: bool
    ratios: list[ShariahRatio]


class ShariahScreening(BaseModel):
    halal_badge: str = Field(description="PASS, FAIL, or DOUBTFUL")
    aaoifi: ShariahLevel
    strict: ShariahLevel
    summary: str


# ── Fundamentals ──────────────────────────────────────────────────

class Fundamentals(BaseModel):
    market_cap: Optional[float] = None
    pe_ratio: Optional[float] = None
    peg_ratio: Optional[float] = None
    roe: Optional[float] = None
    roa: Optional[float] = None
    net_margin: Optional[float] = None
    gross_margin: Optional[float] = None
    fcf_margin: Optional[float] = None
    debt_to_equity: Optional[float] = None
    revenue_cagr_3y: Optional[float] = None
    currency: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    name: Optional[str] = None
    website: Optional[str] = None


# ── Technicals ────────────────────────────────────────────────────

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


# ── AI Analysis ───────────────────────────────────────────────────

class AISection(BaseModel):
    rating: Optional[str] = None
    summary: str
    details: list[str] = []


class EntryZone(BaseModel):
    low: Optional[float] = None
    high: Optional[float] = None


class AIVerdict(BaseModel):
    shariah_compliance: Optional[AISection] = None
    company_quality: Optional[AISection] = None
    valuation: Optional[AISection] = None
    entry_timing: Optional[AISection] = None
    final_verdict: Optional[str] = Field(None, description="BUY / WAIT / AVOID")
    entry_zone: Optional[EntryZone] = None
    stop_loss: Optional[float] = None
    target_18m: Optional[float] = None
    raw_response: Optional[str] = Field(None, description="Raw Claude JSON if parsing fails")


# ── Aggregated Ticker Response ────────────────────────────────────

class TickerResponse(BaseModel):
    symbol: str
    fundamentals: Fundamentals
    technicals: Technicals
    shariah: ShariahScreening
    ai_verdict: Optional[AIVerdict] = None
    errors: list[str] = Field(default_factory=list, description="Non-fatal warnings")
