import re
from typing import Optional

import httpx

from app.core.logging import logger

MUSAFFA_BASE = "https://musaffa.com/stocks"

# Known haram industry keywords for segment classification
HARAM_KEYWORDS = [
    "alcohol", "beer", "wine", "spirits", "liquor", "brewing",
    "tobacco", "cigarette",
    "gambling", "casino", "betting", "lottery",
    "weapon", "defense", "arms", "ammunition", "military",
    "pork", "swine",
    "adult entertainment", "pornograph",
    "interest income", "interest earned",
    "cannabis", "marijuana",
]


def _is_haram_segment(segment_name: str) -> bool:
    """Check if a revenue segment name matches known haram categories."""
    lower = segment_name.lower()
    return any(kw in lower for kw in HARAM_KEYWORDS)


async def scrape_revenue_breakdown(symbol: str) -> Optional[dict]:
    """
    Scrape Musaffa's stock page to extract the Revenue Breakdown table.

    Returns:
        {
            "segments": {"SegmentName": revenue_float, ...},
            "haram_segments": {"SegmentName": revenue_float, ...},
            "haram_total": float,
            "total_revenue": float,
            "source": "musaffa",
        }
        or None if scraping fails.
    """
    url = f"{MUSAFFA_BASE}/{symbol.upper()}"
    logger.info("Musaffa: scraping revenue breakdown for %s at %s", symbol, url)

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)

        if resp.status_code != 200:
            logger.warning("Musaffa returned %d for %s", resp.status_code, symbol)
            return None

        html = resp.text

        # Look for revenue breakdown table data.
        # Musaffa typically renders segment data in structured elements.
        # We parse simple patterns: segment names with numeric values.
        segments: dict[str, float] = {}

        # Pattern: look for revenue segment rows with name and value
        # Handles formats like "Segment Name ... $1,234,567" or "1234567"
        segment_pattern = re.compile(
            r'(?:revenue[_\s-]*(?:breakdown|segment|product))'
            r'.*?'
            r'([A-Za-z][A-Za-z &,\'-]{2,50})\s*'
            r'[\$]?\s*([\d,]+(?:\.\d+)?)\s*(?:M|B|K|million|billion)?',
            re.IGNORECASE | re.DOTALL,
        )

        # Broader fallback: look for any table-like segment data
        row_pattern = re.compile(
            r'<(?:td|span|div)[^>]*>\s*([A-Za-z][A-Za-z &,\'-]{2,50}?)\s*</'
            r'.*?'
            r'<(?:td|span|div)[^>]*>\s*[\$]?\s*([\d,]+(?:\.\d+)?)\s*',
            re.IGNORECASE | re.DOTALL,
        )

        # Try the targeted pattern first
        matches = segment_pattern.findall(html)
        if not matches:
            matches = row_pattern.findall(html)

        for name, value_str in matches:
            name = name.strip()
            try:
                value = float(value_str.replace(",", ""))
                if value > 0:
                    segments[name] = value
            except ValueError:
                continue

        if not segments:
            logger.warning("Musaffa: no revenue segments found for %s", symbol)
            return None

        haram_segments = {k: v for k, v in segments.items() if _is_haram_segment(k)}
        haram_total = sum(haram_segments.values())
        total_revenue = sum(segments.values())

        logger.info(
            "Musaffa: found %d segments for %s (%d haram, total=%.0f)",
            len(segments), symbol, len(haram_segments), total_revenue,
        )

        return {
            "segments": segments,
            "haram_segments": haram_segments,
            "haram_total": haram_total,
            "total_revenue": total_revenue,
            "source": "musaffa",
        }

    except Exception as exc:
        logger.warning("Musaffa scraping failed for %s: %s", symbol, exc)
        return None
