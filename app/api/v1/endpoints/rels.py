"""
Corporate Relationships Endpoint — GLEIF-powered ownership tree.

Resolves a ticker's LEI via GLEIF fuzzycompletions, then builds:
  - Entity identity (LEI, legal name, country)
  - Ultimate parent (null if entity is root)
  - Direct subsidiaries, level 1 (up to 20)
  - Sub-subsidiaries, level 2 (first 5 L1 children, up to 5 each)

All GLEIF calls are timeout-guarded (10s). Partial results are returned
on timeout rather than erroring.

Route:
    GET /rels/{symbol}
"""

import asyncio
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException

from app.core.cache import cache_get, cache_set
from app.core.logging import logger
from app.core.security import rate_limit_dependency
from app.integration import yfinance_client


router = APIRouter()

_RELS_CACHE_NS = "rels"
_RELS_CACHE_TTL = 604_800  # 7 days

_GLEIF_BASE = "https://api.gleif.org/api/v1"
_GLEIF_HEADERS = {"Accept": "application/vnd.api+json"}
_GLEIF_TIMEOUT = 10.0

# Common yfinance country names → ISO 3166-1 alpha-2
_COUNTRY_MAP: dict[str, str] = {
    "united states": "US", "usa": "US",
    "united kingdom": "GB", "uk": "GB",
    "france": "FR", "germany": "DE", "japan": "JP",
    "china": "CN", "canada": "CA", "australia": "AU",
    "switzerland": "CH", "netherlands": "NL", "sweden": "SE",
    "south korea": "KR", "india": "IN", "brazil": "BR",
    "spain": "ES", "italy": "IT", "hong kong": "HK",
    "singapore": "SG", "ireland": "IE", "denmark": "DK",
    "norway": "NO", "finland": "FI", "belgium": "BE",
    "austria": "AT", "israel": "IL", "taiwan": "TW",
}


def _to_iso2(country_raw: str | None) -> str | None:
    if not country_raw:
        return None
    s = country_raw.strip()
    if len(s) == 2:
        return s.upper()
    return _COUNTRY_MAP.get(s.lower())


def _lei_name(legal_name_field) -> str:
    """GLEIF legalName can be a str or {"name": str, "language": str}."""
    if isinstance(legal_name_field, dict):
        return legal_name_field.get("name") or ""
    return legal_name_field or ""


async def _gleif_get(
    client: httpx.AsyncClient,
    url: str,
    params: dict | None = None,
) -> dict | None:
    """Single GLEIF GET — returns None on 404, timeout, or any HTTP error."""
    try:
        resp = await client.get(
            url,
            params=params,
            headers=_GLEIF_HEADERS,
            timeout=_GLEIF_TIMEOUT,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException:
        logger.warning("GLEIF timeout: %s", url)
        return None
    except Exception as exc:
        logger.warning("GLEIF error (%s): %s", url, exc)
        return None


def _similarity(a: str, b: str) -> float:
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


_SIMILARITY_THRESHOLD = 0.6


async def _search_lei(
    client: httpx.AsyncClient,
    legal_name: str,
    iso2: str | None,
) -> dict | None:
    """
    Resolve a company name to its LEI using fuzzycompletions + similarity scoring.

    Strategy:
      1. Fuzzycompletions → up to 10 LEI candidates.
      2. Fetch all full records in parallel for name + country.
      3. Score each record with SequenceMatcher against legal_name.
      4. Filter by country match (when available) and score >= 0.6.
      5. Return the highest-scoring candidate; None if none clears threshold.
    """
    fuzz = await _gleif_get(
        client,
        f"{_GLEIF_BASE}/fuzzycompletions",
        params={"field": "entity.legalName", "q": legal_name, "page[size]": "10"},
    )
    if not fuzz or not fuzz.get("data"):
        return None

    candidates: list[str] = []
    for item in fuzz["data"]:
        rel = (item.get("relationships") or {}).get("lei-records", {}).get("data", {})
        lei = rel.get("id") if isinstance(rel, dict) else None
        if lei:
            candidates.append(lei)

    if not candidates:
        return None

    # Fetch full records in parallel
    tasks = [
        _gleif_get(client, f"{_GLEIF_BASE}/lei-records/{lei}")
        for lei in candidates
    ]
    records_raw = await asyncio.gather(*tasks)

    scored: list[tuple[float, dict]] = []
    for raw in records_raw:
        if not raw or not raw.get("data"):
            continue
        rec = raw["data"]
        attrs = rec.get("attributes", {})
        entity = attrs.get("entity", {})
        rec_country = (entity.get("legalAddress") or {}).get("country")
        rec_name = _lei_name(entity.get("legalName"))

        # Country filter: skip records from wrong country when iso2 is known
        if iso2 and rec_country and rec_country.upper() != iso2.upper():
            continue

        score = _similarity(legal_name, rec_name)
        if score < _SIMILARITY_THRESHOLD:
            continue

        scored.append((score, {
            "lei": rec.get("id"),
            "name": rec_name,
            "country": rec_country,
            "status": entity.get("status", "ACTIVE"),
        }))

    if not scored:
        return None

    scored.sort(key=lambda x: x[0], reverse=True)
    best_score, best_entry = scored[0]

    logger.info(
        "GLEIF match: %s → %s (score=%.2f, lei=%s)",
        legal_name, best_entry["name"], best_score, best_entry["lei"],
    )
    return best_entry


async def _get_ultimate_parent(
    client: httpx.AsyncClient,
    lei: str,
) -> dict | None:
    """Returns ultimate parent dict, or None if entity is root (404)."""
    raw = await _gleif_get(client, f"{_GLEIF_BASE}/lei-records/{lei}/ultimate-parent")
    if not raw:
        return None

    data = raw.get("data")
    if not data:
        return None

    rec = data[0] if isinstance(data, list) else data
    parent_lei = rec.get("id")
    attrs = rec.get("attributes", {})
    entity = attrs.get("entity", {})

    return {
        "lei": parent_lei,
        "name": _lei_name(entity.get("legalName")),
        "country": (entity.get("legalAddress") or {}).get("country"),
        "is_self": parent_lei == lei,
    }


async def _get_children(
    client: httpx.AsyncClient,
    lei: str,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """
    Returns (children_list, total_count) using the GLEIF /direct-children path.

    Falls back to the filter[relationships.directParent.data.id] query if the
    path endpoint returns an empty dataset (API version variance guard).
    """
    # Primary: official GLEIF relationship endpoint
    raw = await _gleif_get(
        client,
        f"{_GLEIF_BASE}/lei-records/{lei}/direct-children",
        params={"page[size]": str(page_size)},
    )
    logger.info("GLEIF children raw (direct-children): %s", raw)

    # Fallback: filter query (some GLEIF API versions expose this)
    if not raw or not raw.get("data"):
        raw = await _gleif_get(
            client,
            f"{_GLEIF_BASE}/lei-records",
            params={
                "filter[relationships.directParent.data.id]": lei,
                "page[size]": str(page_size),
            },
        )
        logger.info("GLEIF children raw (filter fallback): %s", raw)

    if not raw:
        return [], 0

    total = (
        (raw.get("meta") or {})
        .get("pagination", {})
        .get("total", 0)
    )

    children: list[dict] = []
    for rec in raw.get("data") or []:
        attrs = rec.get("attributes", {})
        entity = attrs.get("entity", {})
        children.append({
            "lei": rec.get("id"),
            "name": _lei_name(entity.get("legalName")),
            "country": (entity.get("legalAddress") or {}).get("country"),
            "status": entity.get("status", ""),
        })

    return children, total


async def _get_children_safe(
    client: httpx.AsyncClient,
    lei: str,
    page_size: int = 5,
) -> list[dict]:
    """Level-2 fetch with silent timeout — returns [] on any error."""
    try:
        children, _ = await asyncio.wait_for(
            _get_children(client, lei, page_size=page_size),
            timeout=_GLEIF_TIMEOUT,
        )
        return children
    except asyncio.TimeoutError:
        logger.warning("GLEIF L2 timeout for lei=%s", lei)
        return []


@router.get(
    "/rels/{symbol}",
    summary="Corporate Relationships (GLEIF)",
    description=(
        "Returns the corporate ownership tree: LEI identity, ultimate parent, "
        "direct subsidiaries (L1, max 20), and sub-subsidiaries (L2, first 5×5). "
        "Data sourced from GLEIF. Cache TTL 7 days."
    ),
    dependencies=[Depends(rate_limit_dependency)],
)
async def corporate_rels(symbol: str) -> dict:
    symbol = symbol.upper().strip()
    if not symbol.isalnum() and "." not in symbol and "-" not in symbol:
        raise HTTPException(status_code=400, detail="Invalid ticker symbol")

    # ── Network connectivity probe (temporary diagnostic) ─────────
    try:
        async with httpx.AsyncClient(timeout=8) as _probe:
            r1 = await _probe.get(
                "https://api.opencorporates.com/v0.4/companies/search?q=Apple&jurisdiction_code=us",
                headers={"User-Agent": "FinTerminal/1.0"},
            )
            logger.info("OpenCorporates: %s", r1.status_code)

            r2 = await _probe.get(
                "https://api.gleif.org/api/v1/lei-records?filter[entity.legalName]=Apple",
                headers={"Accept": "application/vnd.api+json"},
            )
            logger.info("GLEIF direct: %s", r2.status_code)
    except Exception as e:
        logger.error("Network test failed: %s", e)

    cached = cache_get(_RELS_CACHE_NS, symbol)
    if cached is not None:
        logger.info("rels/%s: cache hit", symbol)
        return cached

    # ── Step 1: Legal name + country from raw yfinance info ───────
    try:
        info = await yfinance_client.get_ticker_info(symbol)
    except Exception as exc:
        logger.error("rels/%s: ticker info failed: %s", symbol, exc)
        raise HTTPException(
            status_code=502,
            detail=f"Could not fetch ticker data for '{symbol}': {exc}",
        )

    legal_name: str | None = info.get("longName") or info.get("shortName")
    if not legal_name:
        raise HTTPException(status_code=404, detail=f"No legal name found for '{symbol}'")

    iso2 = _to_iso2(info.get("country"))

    partial = False

    async with httpx.AsyncClient() as client:
        # ── Step 2: Resolve LEI ───────────────────────────────────
        entity_info = await _search_lei(client, legal_name, iso2)

        if not entity_info or not entity_info.get("lei"):
            result = {
                "symbol": symbol,
                "entity": None,
                "ultimate_parent": None,
                "children": [],
                "total_children": 0,
                "available": False,
                "partial": False,
            }
            cache_set(_RELS_CACHE_NS, symbol, result, ttl=_RELS_CACHE_TTL)
            return result

        lei = entity_info["lei"]

        # ── Step 3 + 4: Parent and L1 children in parallel ────────
        parent_task = _get_ultimate_parent(client, lei)
        children_task = _get_children(client, lei, page_size=20)

        try:
            (ultimate_parent, (l1_children, total_children)) = await asyncio.gather(
                parent_task, children_task
            )
        except asyncio.TimeoutError:
            logger.warning("rels/%s: GLEIF gather timeout", symbol)
            ultimate_parent = None
            l1_children = []
            total_children = 0
            partial = True

        # ── Step 5: L2 sub-children (first 5 L1 only) ────────────
        l2_tasks = [
            _get_children_safe(client, child["lei"], page_size=5)
            for child in l1_children[:5]
            if child.get("lei")
        ]
        if l2_tasks:
            l2_results = await asyncio.gather(*l2_tasks)
            for i, sub_children in enumerate(l2_results):
                l1_children[i]["children"] = sub_children
        # Remaining L1 children (beyond index 5) get empty children list
        for child in l1_children[5:]:
            child["children"] = []
        for child in l1_children[:5]:
            if "children" not in child:
                child["children"] = []

    # ── Step 6: Build response ─────────────────────────────────────
    result = {
        "symbol": symbol,
        "entity": {
            "lei": entity_info["lei"],
            "name": entity_info["name"],
            "country": entity_info["country"],
            "is_root": ultimate_parent is None,
        },
        "ultimate_parent": ultimate_parent or {
            "lei": None, "name": None, "country": None, "is_self": True
        },
        "children": l1_children,
        "total_children": total_children,
        "available": True,
        "partial": partial,
    }

    cache_set(_RELS_CACHE_NS, symbol, result, ttl=_RELS_CACHE_TTL)
    logger.info(
        "rels/%s: lei=%s parent=%s children=%d/%d partial=%s",
        symbol, lei,
        (ultimate_parent or {}).get("name"),
        len(l1_children), total_children, partial,
    )
    return result
