from pydantic import BaseModel
from typing import Optional


class TickerInfo(BaseModel):
    """Minimal ticker validation response."""
    symbol: str
    name: Optional[str] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    currency: Optional[str] = None
    website: Optional[str] = None


class PriceResponse(BaseModel):
    """Lightweight price snapshot."""
    current_price: float
    change: float
    change_pct: float
