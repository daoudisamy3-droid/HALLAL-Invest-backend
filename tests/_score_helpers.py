"""Shared test helpers for Step 4 score tests.

Builds SEC-shaped ``company_facts`` payloads with controlled multi-year
data, so tests can assert exact Decimal arithmetic on known inputs.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


# Unit per US-GAAP tag (USD by default).
_TAG_UNITS: dict[str, str] = {
    "EarningsPerShareDiluted": "USD/shares",
    "CommonStockSharesOutstanding": "shares",
    "EntityCommonStockSharesOutstanding": "shares",
}


def fact(end: str, val: Decimal | int | float, *, fy: int | None = None,
         fp: str = "FY", form: str = "10-K", filed: str | None = None,
         accn: str | None = None) -> dict[str, Any]:
    """Build a single fact entry compatible with SEC ``company_facts`` shape."""
    if fy is None:
        fy = int(end[:4])
    if filed is None:
        filed = end
    if accn is None:
        accn = f"0000000000-{fy}-000001"
    return {
        "start": f"{fy - 1}-01-01",
        "end": end,
        "val": float(val) if isinstance(val, Decimal) else val,
        "accn": accn,
        "fy": fy,
        "fp": fp,
        "form": form,
        "filed": filed,
    }


def make_facts(
    concepts: dict[str, list[tuple[str, Any]]],
    *,
    entity_name: str = "Test Corp",
    cik: int = 999_999,
) -> dict[str, Any]:
    """Build a full SEC ``company_facts`` payload.

    ``concepts`` maps GAAP-tag → list of (end_date_iso, value) tuples.
    Each value is wrapped as a FY (annual) entry. Unit is inferred from
    ``_TAG_UNITS`` (default USD).
    """
    us_gaap: dict[str, Any] = {}
    for tag, entries in concepts.items():
        unit = _TAG_UNITS.get(tag, "USD")
        rows = [fact(end, val) for end, val in entries]
        us_gaap[tag] = {"label": tag, "units": {unit: rows}}
    return {
        "cik": cik,
        "entityName": entity_name,
        "facts": {"us-gaap": us_gaap},
    }


def make_submissions(
    sic_code: int = 7372,
    sic_description: str = "Services-Prepackaged Software",
    *,
    forms: list[tuple[str, str]] | None = None,  # [(form, filing_date), ...]
) -> dict[str, Any]:
    """Build a minimal SEC submissions payload with a recent filings array."""
    forms = forms or []
    return {
        "cik": "0000999999",
        "entityType": "operating",
        "sic": str(sic_code),
        "sicCode": str(sic_code),
        "sicDescription": sic_description,
        "name": "Test Corp",
        "filings": {
            "recent": {
                "form": [f for f, _ in forms],
                "filingDate": [d for _, d in forms],
                "accessionNumber": [f"0000000000-{i:04d}" for i in range(len(forms))],
            },
            "files": [],
        },
    }
