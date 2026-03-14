#!/usr/bin/env python3
"""
Extract real oil & gas infrastructure from OpenStreetMap via Overpass API.

Queries:
  1. Global offshore platforms: node["man_made"="offshore_platform"]
  2. Onshore wells in 3 major bounding boxes:
     - Texas / Permian Basin
     - North Sea / Europe
     - Middle East / Persian Gulf

Output: app/data/osm_energy_facilities.json
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("[error] 'requests' is required. Install with: pip install requests")
    sys.exit(1)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OUTPUT = Path(__file__).resolve().parent.parent / "app" / "data" / "osm_energy_facilities.json"

HEADERS = {"User-Agent": "HALLAL-Invest-ETL/1.0 (educational project)"}
TIMEOUT = 120  # seconds per query


# ── Overpass queries ─────────────────────────────────────────────

QUERIES = [
    # 1. All offshore platforms worldwide
    {
        "label": "Offshore platforms (global)",
        "type": "offshore",
        "query": '[out:json][timeout:90];node["man_made"="offshore_platform"];out center;',
    },
    # 2. Onshore wells – Texas / Permian / Eagle Ford / Gulf states
    {
        "label": "Onshore wells – Texas & US Gulf",
        "type": "onshore",
        "query": '[out:json][timeout:90];node["industrial"="oil_well"](25.0,-107.0,37.0,-93.0);out center;',
    },
    # 3. Onshore wells – North Sea / Northwest Europe
    {
        "label": "Onshore wells – NW Europe",
        "type": "onshore",
        "query": '[out:json][timeout:90];node["industrial"="oil_well"](50.0,-5.0,62.0,15.0);out center;',
    },
    # 4. Onshore wells – Middle East / Persian Gulf
    {
        "label": "Onshore wells – Middle East",
        "type": "onshore",
        "query": '[out:json][timeout:90];node["industrial"="oil_well"](20.0,40.0,38.0,60.0);out center;',
    },
    # 5. Onshore wells – Caspian / Central Asia
    {
        "label": "Onshore wells – Caspian",
        "type": "onshore",
        "query": '[out:json][timeout:90];node["industrial"="oil_well"](38.0,46.0,52.0,60.0);out center;',
    },
    # 6. Onshore wells – Western Siberia
    {
        "label": "Onshore wells – W. Siberia",
        "type": "onshore",
        "query": '[out:json][timeout:90];node["industrial"="oil_well"](55.0,60.0,68.0,90.0);out center;',
    },
    # 7. Onshore wells – North Dakota / Bakken
    {
        "label": "Onshore wells – Bakken / N. Dakota",
        "type": "onshore",
        "query": '[out:json][timeout:90];node["industrial"="oil_well"](46.0,-105.0,49.0,-97.0);out center;',
    },
    # 8. Onshore wells – Alberta / Canada
    {
        "label": "Onshore wells – Alberta",
        "type": "onshore",
        "query": '[out:json][timeout:90];node["industrial"="oil_well"](49.0,-120.0,58.0,-110.0);out center;',
    },
    # 9. Petroleum wells worldwide (petroleum_well tag, broader)
    {
        "label": "Petroleum wells (global)",
        "type": "onshore",
        "query": '[out:json][timeout:90];node["man_made"="petroleum_well"];out center;',
    },
]


def _run_query(label: str, query: str, site_type: str) -> list[dict]:
    """Execute one Overpass query and return normalised features."""
    print(f"  [{label}] querying Overpass ...")
    try:
        resp = requests.post(
            OVERPASS_URL,
            data={"data": query},
            headers=HEADERS,
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        print(f"  [{label}] TIMEOUT after {TIMEOUT}s – skipping")
        return []
    except requests.exceptions.HTTPError as exc:
        status = exc.response.status_code if exc.response else "?"
        if status == 429:
            print(f"  [{label}] rate-limited (429), waiting 30s ...")
            time.sleep(30)
            return _run_query(label, query, site_type)  # one retry
        print(f"  [{label}] HTTP {status} – skipping")
        return []
    except Exception as exc:
        print(f"  [{label}] error: {exc} – skipping")
        return []

    elements = data.get("elements", [])
    features = []
    for el in elements:
        lat = el.get("lat")
        lon = el.get("lon")
        if lat is None or lon is None:
            # Try center coords (for ways/relations)
            center = el.get("center", {})
            lat = center.get("lat")
            lon = center.get("lon")
        if lat is None or lon is None:
            continue

        tags = el.get("tags", {})
        name = (
            tags.get("name")
            or tags.get("name:en")
            or tags.get("operator")
            or tags.get("description")
            or ""
        )

        features.append({
            "lat": round(lat, 5),
            "lng": round(lon, 5),
            "type": site_type,
            "name": name,
            "osm_id": el.get("id"),
        })

    print(f"  [{label}] got {len(features)} features")
    return features


def main() -> None:
    print(f"[osm-etl] Overpass API extraction – {len(QUERIES)} queries")
    print(f"[osm-etl] Output: {OUTPUT}\n")

    all_features: list[dict] = []
    seen_ids: set[int] = set()

    for i, q in enumerate(QUERIES):
        if i > 0:
            # Rate-limit: wait between queries to be polite to Overpass
            print("  (waiting 5s between queries ...)")
            time.sleep(5)

        features = _run_query(q["label"], q["query"], q["type"])

        # Deduplicate by osm_id
        for f in features:
            oid = f.get("osm_id")
            if oid and oid in seen_ids:
                continue
            if oid:
                seen_ids.add(oid)
            all_features.append(f)

    # Build final output
    result = {
        "source": "OpenStreetMap / Overpass API",
        "total": len(all_features),
        "types": {
            "offshore": sum(1 for f in all_features if f["type"] == "offshore"),
            "onshore": sum(1 for f in all_features if f["type"] == "onshore"),
        },
        "features": all_features,
    }

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(result, fh, separators=(",", ":"))

    size_mb = OUTPUT.stat().st_size / (1024 * 1024)
    print(f"\n[osm-etl] Done: {result['total']} features "
          f"({result['types']['offshore']} offshore, {result['types']['onshore']} onshore)")
    print(f"[osm-etl] Written to {OUTPUT} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
