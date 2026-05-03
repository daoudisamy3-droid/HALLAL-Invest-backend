"""
Portfolio Tracker — persistent position management with real-time P&L.

Routes:
    GET    /portfolio                              → full portfolio + live P&L
    POST   /portfolio/position                     → add position or transaction
    DELETE /portfolio/position/{symbol}            → delete entire position
    DELETE /portfolio/transaction/{symbol}/{idx}   → delete single transaction
    PUT    /portfolio/settings                     → update DCA budget / display
    GET    /portfolio/history/{symbol}             → OHLCV bars + entry markers

Data stored as a single JSON blob in Redis / in-memory cache.
Namespace: "portfolio", Key: "positions", TTL: 1 year.
"""

import asyncio
import uuid
from datetime import datetime
from typing import Optional

import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator

from app.core.cache import cache_get, cache_set
from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.integration import yfinance_client

router = APIRouter()

_PORTFOLIO_NS = "portfolio"
_PORTFOLIO_KEY = "positions"
_PORTFOLIO_TTL = 365 * 86_400  # 1 year (persistent)


# ── Storage ───────────────────────────────────────────────────────

def _load_portfolio() -> dict:
    data = cache_get(_PORTFOLIO_NS, _PORTFOLIO_KEY)
    if data is None:
        return {
            "positions": [],
            "settings": {"monthly_budget": 0.0, "currency_display": "USD"},
        }
    return data


def _save_portfolio(data: dict) -> None:
    cache_set(_PORTFOLIO_NS, _PORTFOLIO_KEY, data, ttl=_PORTFOLIO_TTL)


# ── Calculation helpers ───────────────────────────────────────────

def _compute_position_stats(transactions: list) -> dict:
    """
    Derives total_shares, avg_price, total_cost, total_fees from raw transactions.
    BUY increases position; SELL decreases shares only (cost basis unchanged).
    """
    total_shares = 0.0
    total_cost = 0.0
    total_fees = 0.0

    for t in transactions:
        if t["type"] == "BUY":
            total_shares += t["shares"]
            total_cost += t["shares"] * t["price"] + t.get("fees", 0)
            total_fees += t.get("fees", 0)
        elif t["type"] == "SELL":
            total_shares -= t["shares"]

    avg_price = total_cost / total_shares if total_shares > 0 else 0.0

    return {
        "total_shares": round(total_shares, 8),
        "avg_price": round(avg_price, 4),
        "total_cost": round(total_cost, 4),
        "total_fees": round(total_fees, 4),
    }


def _yf_symbol(symbol: str, currency: str) -> str:
    """Map a plain symbol to its yfinance ticker (EUR stocks → XETRA suffix)."""
    if currency == "EUR" and "." not in symbol:
        return f"{symbol}.DE"
    return symbol


def _fetch_fx_rate_blocking() -> float:
    """Blocking yfinance call for EURUSD=X — run in executor."""
    try:
        fi = yf.Ticker("EURUSD=X").fast_info
        return float(fi["lastPrice"])
    except Exception:
        return 1.10  # reasonable fallback


async def _get_current_price_and_fx(symbol: str, currency: str) -> dict:
    """
    Returns price_native, price_usd, fx_rate, change_pct.
    EUR stocks get .DE suffix; fx_rate fetched from EURUSD=X.
    """
    yf_sym = _yf_symbol(symbol, currency)
    loop = asyncio.get_running_loop()

    fx_rate = 1.0
    if currency == "EUR":
        try:
            fx_rate = await loop.run_in_executor(None, _fetch_fx_rate_blocking)
        except Exception:
            fx_rate = 1.10

    try:
        price_data = await yfinance_client.get_fast_price(yf_sym)
        price_native = float(price_data.get("current_price") or 0)
        change_pct = float(price_data.get("change_pct") or 0)
    except Exception as exc:
        logger.warning("portfolio: price fetch failed for %s: %s", yf_sym, exc)
        price_native = 0.0
        change_pct = 0.0

    return {
        "price_native": price_native,
        "price_usd": round(price_native * fx_rate, 4),
        "fx_rate": round(fx_rate, 4),
        "change_pct": round(change_pct, 4),
    }


# ── Validation helpers ────────────────────────────────────────────

def _validate_symbol(symbol: str) -> str:
    s = symbol.upper().strip()
    if not s or len(s) > 10:
        raise HTTPException(status_code=400, detail="Symbol must be 1-10 characters")
    if not all(c.isalnum() or c in (".", "-") for c in s):
        raise HTTPException(status_code=400, detail="Invalid symbol format")
    return s


def _validate_date(date_str: str) -> str:
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid date format: '{date_str}'. Use YYYY-MM-DD",
        )


# ── Pydantic models ───────────────────────────────────────────────

class TransactionBody(BaseModel):
    symbol: str
    name: str
    currency: str = "USD"
    date: str
    shares: float
    price: float
    fees: float = 0.0
    type: str = "BUY"

    @field_validator("shares", "price")
    @classmethod
    def must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("must be positive")
        return v

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, v: str) -> str:
        if v.upper() not in ("USD", "EUR"):
            raise ValueError("currency must be USD or EUR")
        return v.upper()

    @field_validator("type")
    @classmethod
    def valid_type(cls, v: str) -> str:
        if v.upper() not in ("BUY", "SELL"):
            raise ValueError("type must be BUY or SELL")
        return v.upper()


class SettingsBody(BaseModel):
    monthly_budget: float
    currency_display: str = "USD"


# ── Endpoints ─────────────────────────────────────────────────────

@router.get(
    "/portfolio",
    summary="Portfolio with Live P&L",
    description=(
        "Returns all positions enriched with real-time prices and unrealised "
        "P&L in USD. EUR stocks are converted via live EURUSD=X rate."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_portfolio() -> dict:
    portfolio = _load_portfolio()
    positions = portfolio.get("positions", [])
    settings = portfolio.get("settings", {"monthly_budget": 0.0, "currency_display": "USD"})

    # Fetch all prices in parallel
    price_tasks = [
        _get_current_price_and_fx(pos["symbol"], pos.get("currency", "USD"))
        for pos in positions
    ]
    prices = await asyncio.gather(*price_tasks, return_exceptions=True)

    enriched = []
    agg_value_usd = 0.0
    agg_cost_usd = 0.0
    agg_fees_usd = 0.0

    for pos, price_result in zip(positions, prices):
        if isinstance(price_result, Exception):
            price_result = {
                "price_native": 0.0, "price_usd": 0.0,
                "fx_rate": 1.0, "change_pct": 0.0,
            }

        stats = _compute_position_stats(pos.get("transactions", []))
        fx = price_result["fx_rate"]

        price_native = price_result["price_native"]
        price_usd = price_result["price_usd"]
        total_shares = stats["total_shares"]
        avg_price = stats["avg_price"]
        total_cost_native = stats["total_cost"]

        current_value_usd = total_shares * price_usd
        total_cost_usd = total_cost_native * fx
        avg_price_usd = avg_price * fx
        pnl_usd = current_value_usd - total_cost_usd
        pnl_pct = (pnl_usd / total_cost_usd * 100) if total_cost_usd > 0 else 0.0

        agg_value_usd += current_value_usd
        agg_cost_usd += total_cost_usd
        agg_fees_usd += stats["total_fees"] * fx

        enriched.append({
            "id": pos["id"],
            "symbol": pos["symbol"],
            "name": pos.get("name", ""),
            "currency": pos.get("currency", "USD"),
            "total_shares": stats["total_shares"],
            "avg_price": stats["avg_price"],
            "avg_price_usd": round(avg_price_usd, 4),
            "current_price": round(price_native, 4),
            "current_price_usd": round(price_usd, 4),
            "current_value_usd": round(current_value_usd, 2),
            "total_cost_usd": round(total_cost_usd, 2),
            "pnl_usd": round(pnl_usd, 2),
            "pnl_pct": round(pnl_pct, 2),
            "change_today_pct": price_result["change_pct"],
            "fx_rate": price_result["fx_rate"],
            "transactions": pos.get("transactions", []),
            "total_fees": stats["total_fees"],
        })

    total_pnl_usd = agg_value_usd - agg_cost_usd
    total_pnl_pct = (total_pnl_usd / agg_cost_usd * 100) if agg_cost_usd > 0 else 0.0

    return {
        "positions": enriched,
        "summary": {
            "total_value_usd": round(agg_value_usd, 2),
            "total_cost_usd": round(agg_cost_usd, 2),
            "total_pnl_usd": round(total_pnl_usd, 2),
            "total_pnl_pct": round(total_pnl_pct, 2),
            "total_fees_usd": round(agg_fees_usd, 2),
            "positions_count": len(enriched),
        },
        "settings": settings,
    }


@router.post(
    "/portfolio/position",
    summary="Add Position or Transaction",
    description=(
        "Adds a BUY/SELL transaction to an existing position, or creates a new "
        "position if the symbol is not yet tracked."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def add_position(body: TransactionBody) -> dict:
    symbol = _validate_symbol(body.symbol)
    _validate_date(body.date)

    portfolio = _load_portfolio()
    positions = portfolio["positions"]

    transaction = {
        "date": body.date,
        "shares": round(body.shares, 8),
        "price": round(body.price, 4),
        "fees": round(body.fees, 4),
        "type": body.type,
    }

    existing = next((p for p in positions if p["symbol"] == symbol), None)

    if existing is not None:
        existing["transactions"].append(transaction)
        if body.name:
            existing["name"] = body.name
        pos = existing
        action = "updated"
    else:
        pos = {
            "id": str(uuid.uuid4()),
            "symbol": symbol,
            "name": body.name,
            "currency": body.currency,
            "transactions": [transaction],
        }
        positions.append(pos)
        action = "created"

    _save_portfolio(portfolio)
    stats = _compute_position_stats(pos["transactions"])

    logger.info(
        "portfolio: %s %s %s — %.8f shares @ %.4f",
        action, body.type, symbol, body.shares, body.price,
    )

    return {
        "id": pos["id"],
        "symbol": pos["symbol"],
        "name": pos["name"],
        "currency": pos["currency"],
        "transactions": pos["transactions"],
        **stats,
    }


@router.delete(
    "/portfolio/position/{symbol}",
    summary="Delete Position",
    description="Removes a position and all its transactions permanently.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def delete_position(symbol: str) -> dict:
    symbol = _validate_symbol(symbol)
    portfolio = _load_portfolio()
    before = len(portfolio["positions"])
    portfolio["positions"] = [
        p for p in portfolio["positions"] if p["symbol"] != symbol
    ]

    if len(portfolio["positions"]) == before:
        raise HTTPException(status_code=404, detail=f"Position '{symbol}' not found")

    _save_portfolio(portfolio)
    logger.info("portfolio: deleted position %s", symbol)
    return {"deleted": symbol}


@router.delete(
    "/portfolio/transaction/{symbol}/{transaction_index}",
    summary="Delete Transaction",
    description="Removes a transaction by index (0-based). Deletes the position if it becomes empty.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def delete_transaction(symbol: str, transaction_index: int) -> dict:
    symbol = _validate_symbol(symbol)
    portfolio = _load_portfolio()

    pos = next((p for p in portfolio["positions"] if p["symbol"] == symbol), None)
    if pos is None:
        raise HTTPException(status_code=404, detail=f"Position '{symbol}' not found")

    txs = pos.get("transactions", [])
    if transaction_index < 0 or transaction_index >= len(txs):
        raise HTTPException(
            status_code=400,
            detail=f"Transaction index {transaction_index} out of range (0–{len(txs) - 1})",
        )

    removed = txs.pop(transaction_index)

    if not txs:
        portfolio["positions"] = [
            p for p in portfolio["positions"] if p["symbol"] != symbol
        ]
        _save_portfolio(portfolio)
        return {"deleted_transaction": removed, "position_removed": True}

    _save_portfolio(portfolio)
    stats = _compute_position_stats(txs)
    return {"deleted_transaction": removed, "position_removed": False, **stats}


@router.put(
    "/portfolio/settings",
    summary="Update Portfolio Settings",
    description="Updates the monthly DCA budget and display currency preference.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def update_settings(body: SettingsBody) -> dict:
    portfolio = _load_portfolio()
    portfolio["settings"] = {
        "monthly_budget": round(body.monthly_budget, 2),
        "currency_display": body.currency_display.upper(),
    }
    _save_portfolio(portfolio)
    return portfolio["settings"]


@router.get(
    "/portfolio/history/{symbol}",
    summary="Position Price History",
    description=(
        "Returns daily close prices from the first transaction date, "
        "plus transaction markers so the frontend can overlay entry/exit points."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_position_history(symbol: str) -> dict:
    symbol = _validate_symbol(symbol)
    logger.info("history/%s: début fetch", symbol)

    portfolio = _load_portfolio()

    pos = next((p for p in portfolio["positions"] if p["symbol"] == symbol), None)
    if pos is None:
        raise HTTPException(status_code=404, detail=f"Position '{symbol}' not found")

    txs = pos.get("transactions", [])
    currency = pos.get("currency", "USD")
    yf_sym = _yf_symbol(symbol, currency)

    bars: list[dict] = []
    source = "none"

    if txs:
        first_date = min(t["date"] for t in txs)

        # ── Primary: Alpaca via fetch_ohlcv ───────────────────────
        try:
            first_dt = datetime.strptime(first_date, "%Y-%m-%d")
            days_needed = (datetime.now() - first_dt).days + 30
            limit = max(days_needed, 90)

            from app.ml.data_loader import fetch_ohlcv
            df = await fetch_ohlcv(yf_sym, limit=limit)
            if not df.empty and "timestamp" in df.columns:
                df["date_str"] = df["timestamp"].dt.strftime("%Y-%m-%d")
                df_filtered = df[df["date_str"] >= first_date]
                bars = [
                    {"date": row["date_str"], "close": round(float(row["close"]), 4)}
                    for _, row in df_filtered.iterrows()
                ]
                source = "alpaca"
        except Exception as exc:
            logger.warning("history/%s: Alpaca failed (%s), trying YFinance", symbol, exc)

        # ── Fallback: YFinance ────────────────────────────────────
        if not bars:
            try:
                loop = asyncio.get_running_loop()

                def _yf_history() -> list[dict]:
                    ticker = yf.Ticker(yf_sym)
                    hist = ticker.history(start=first_date)
                    if hist.empty:
                        return []
                    return [
                        {"date": str(idx.date()), "close": round(float(row["Close"]), 4)}
                        for idx, row in hist.iterrows()
                    ]

                bars = await loop.run_in_executor(None, _yf_history)
                source = "yfinance"
            except Exception as exc:
                logger.warning("history/%s: YFinance fallback also failed: %s", symbol, exc)

    logger.info("history/%s: %d bars via %s", symbol, len(bars), source)

    if not bars and txs:
        return {
            "symbol": symbol,
            "bars": [],
            "transactions": [
                {"date": t["date"], "price": t["price"],
                 "shares": t["shares"], "type": t["type"]}
                for t in txs
            ],
            "error": "Historique non disponible",
        }

    return {
        "symbol": symbol,
        "bars": bars,
        "transactions": [
            {
                "date": t["date"],
                "price": t["price"],
                "shares": t["shares"],
                "type": t["type"],
            }
            for t in txs
        ],
    }


@router.get(
    "/portfolio/reset",
    summary="Reset Portfolio",
    description="Clears all positions while preserving settings.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def reset_portfolio() -> dict:
    portfolio = _load_portfolio()
    settings = portfolio.get("settings", {"monthly_budget": 0.0, "currency_display": "USD"})
    _save_portfolio({"positions": [], "settings": settings})
    return {"message": "Portfolio réinitialisé", "positions": 0}
