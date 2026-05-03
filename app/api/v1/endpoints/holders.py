"""
Holders Endpoint — institutional, mutual fund, and insider holdings for any ticker.

Route:
    GET /holders/{symbol}

Data sources: YFinance (institutional_holders, mutualfund_holders,
insider_holders, insider_transactions, info).

Cache TTL: 24 h (holdings data changes infrequently).
"""

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.core.cache import cache_get, cache_set
from app.core.logging import logger
from app.core.security import rate_limit_dependency

router = APIRouter()


# ── Blocking fetch ────────────────────────────────────────────────

def _fetch_holders_blocking(symbol: str) -> dict:
    import yfinance as yf

    t = yf.Ticker(symbol)
    result: dict = {}

    # Summary (% held)
    try:
        info = t.info or {}
        result["summary"] = {
            "pct_insider": info.get("heldPercentInsiders"),
            "pct_institutional": info.get("heldPercentInstitutions"),
            "float_shares": info.get("floatShares"),
            "shares_outstanding": info.get("sharesOutstanding"),
            "implied_shares_outstanding": info.get("impliedSharesOutstanding"),
        }
    except Exception as exc:
        logger.warning("holders/summary %s: %s", symbol, exc)
        result["summary"] = {}

    # Institutional holders
    try:
        inst = t.institutional_holders
        if inst is not None and not inst.empty:
            rows = []
            for _, row in inst.iterrows():
                rows.append({
                    "holder": str(row.get("Holder", "")),
                    "shares": int(row.get("Shares", 0)),
                    "date_reported": str(row.get("Date Reported", ""))[:10],
                    "pct_held": float(row.get("pctHeld", 0) or 0),
                    "value": int(row.get("Value", 0) or 0),
                })
            result["institutional"] = rows[:15]
        else:
            result["institutional"] = []
    except Exception as exc:
        logger.warning("holders/institutional %s: %s", symbol, exc)
        result["institutional"] = []

    # Mutual fund holders
    try:
        mf = t.mutualfund_holders
        if mf is not None and not mf.empty:
            rows = []
            for _, row in mf.iterrows():
                rows.append({
                    "holder": str(row.get("Holder", "")),
                    "shares": int(row.get("Shares", 0)),
                    "date_reported": str(row.get("Date Reported", ""))[:10],
                    "pct_held": float(row.get("pctHeld", 0) or 0),
                    "value": int(row.get("Value", 0) or 0),
                })
            result["mutual_funds"] = rows[:10]
        else:
            result["mutual_funds"] = []
    except Exception as exc:
        logger.warning("holders/mutualfunds %s: %s", symbol, exc)
        result["mutual_funds"] = []

    # Insider holders
    try:
        insider = t.insider_holders
        if insider is not None and not insider.empty:
            rows = []
            for _, row in insider.head(10).iterrows():
                rows.append({
                    "name": str(row.get("Name", "")),
                    "relation": str(row.get("Relation", "")),
                    "shares": int(row.get("Shares", 0) or 0),
                    "pct_held": float(row.get("pctHeld", 0) or 0),
                    "value": int(row.get("Value", 0) or 0),
                    "date_reported": str(row.get("Latest Transaction Date", ""))[:10],
                })
            result["insiders"] = rows
        else:
            result["insiders"] = []
    except Exception as exc:
        logger.warning("holders/insiders %s: %s", symbol, exc)
        result["insiders"] = []

    # Insider transactions
    try:
        trans = t.insider_transactions
        if trans is not None and not trans.empty:
            rows = []
            for _, row in trans.head(15).iterrows():
                shares = row.get("Shares", 0)
                rows.append({
                    "insider": str(row.get("Insider", "")),
                    "relation": str(row.get("Relation", "")),
                    "transaction": str(row.get("Transaction", "")),
                    "date": str(row.get("Start Date", ""))[:10],
                    "shares": int(shares or 0),
                    "value": int(row.get("Value", 0) or 0),
                    "type": "BUY" if (shares or 0) > 0 else "SELL",
                })
            result["insider_transactions"] = rows
        else:
            result["insider_transactions"] = []
    except Exception as exc:
        logger.warning("holders/transactions %s: %s", symbol, exc)
        result["insider_transactions"] = []

    # Aggregate signals
    inst_list = result.get("institutional", [])
    trans_list = result.get("insider_transactions", [])

    top3_pct = sum(h["pct_held"] for h in inst_list[:3]) if inst_list else 0
    recent_buys = sum(1 for tx in trans_list[:10] if tx["type"] == "BUY")
    recent_sells = sum(1 for tx in trans_list[:10] if tx["type"] == "SELL")

    if recent_buys > recent_sells * 2:
        insider_signal, insider_color = "ACCUMULATION", "green"
    elif recent_sells > recent_buys * 2:
        insider_signal, insider_color = "DISTRIBUTION", "red"
    else:
        insider_signal, insider_color = "NEUTRE", "gray"

    result["signals"] = {
        "top3_concentration_pct": round(top3_pct * 100, 2),
        "insider_signal": insider_signal,
        "insider_color": insider_color,
        "recent_buys": recent_buys,
        "recent_sells": recent_sells,
        "institutional_count": len(inst_list),
    }

    return result


# ── Endpoint ──────────────────────────────────────────────────────

@router.get(
    "/holders/{symbol}",
    summary="Holdings institutionnels et initiés",
    description=(
        "Retourne les actionnaires institutionnels (top 15), fonds communs (top 10), "
        "initiés, transactions récentes d'initiés et un signal d'accumulation/distribution. "
        "Cache TTL 24h."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def get_holders(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    cached = cache_get("holders", symbol)
    if cached:
        logger.info("holders/%s: cache hit", symbol)
        return cached

    try:
        loop = asyncio.get_running_loop()
        data = await loop.run_in_executor(None, _fetch_holders_blocking, symbol)
        data["symbol"] = symbol
        data["available"] = True

        cache_set("holders", symbol, data, ttl=86400)
        logger.info(
            "holders/%s: inst=%d mf=%d signal=%s",
            symbol,
            len(data.get("institutional", [])),
            len(data.get("mutual_funds", [])),
            data.get("signals", {}).get("insider_signal", "N/A"),
        )
        return data

    except Exception as exc:
        logger.error("holders/%s: %s", symbol, exc)
        raise HTTPException(status_code=502, detail=f"Holdings unavailable for {symbol}")
