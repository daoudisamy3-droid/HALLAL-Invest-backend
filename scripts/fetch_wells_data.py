#!/usr/bin/env python3
"""
Fetch or generate a high-density global oil & gas facilities dataset.

Strategy:
  1. Try downloading a real public dataset (GitHub-hosted GeoJSON/CSV).
  2. On failure, generate a realistic fallback of ~15 000 points spread
     across the world's major producing basins.

Output: app/data/layers/facilities.geojson
"""

from __future__ import annotations

import json
import math
import random
import sys
import urllib.request
from pathlib import Path

OUTPUT = Path(__file__).resolve().parent.parent / "app" / "data" / "layers" / "facilities.geojson"

# ── Public dataset URLs to try (GitHub raw links) ────────────────
REMOTE_SOURCES: list[str] = [
    # Global Energy Monitor – oil/gas extraction tracker (sample)
    "https://raw.githubusercontent.com/GlobalEnergyMonitor/GOGET-sample/main/global_oil_gas_extraction.geojson",
    # Fractracker – US wells (sample)
    "https://raw.githubusercontent.com/FracTracker/data/main/us_oil_gas_wells.geojson",
]


def _try_remote_download() -> dict | None:
    """Attempt to download a real dataset. Returns parsed GeoJSON or None."""
    for url in REMOTE_SOURCES:
        print(f"[fetch] Trying {url} ...")
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "HALLAL-Invest-ETL/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                if isinstance(data, dict) and data.get("type") == "FeatureCollection":
                    n = len(data.get("features", []))
                    if n >= 500:
                        print(f"[fetch] Success: {n} features from {url}")
                        return data
                    print(f"[fetch] Too few features ({n}), skipping")
        except Exception as exc:
            print(f"[fetch] Failed: {exc}")
    return None


# ── Realistic fallback generator ─────────────────────────────────

# Major basin definitions: (center_lat, center_lng, spread_lat, spread_lng,
#                            count, site_type, name_prefix)
BASINS = [
    # Gulf of Mexico – offshore
    (27.5, -90.0, 2.5, 5.0, 2500, "offshore", "GoM Platform"),
    # Permian Basin – onshore
    (31.8, -102.5, 1.5, 2.0, 3000, "onshore", "Permian Well"),
    # Eagle Ford Shale – onshore
    (28.8, -98.5, 0.8, 2.0, 1200, "onshore", "Eagle Ford Well"),
    # Bakken Formation – onshore
    (48.0, -103.5, 1.0, 1.5, 800, "onshore", "Bakken Well"),
    # North Sea – offshore
    (58.0, 2.0, 4.0, 3.0, 1500, "offshore", "North Sea Platform"),
    # Persian Gulf – offshore
    (26.5, 51.0, 2.0, 3.0, 1200, "offshore", "Gulf Platform"),
    # Saudi Arabia onshore
    (24.5, 47.0, 3.0, 4.0, 800, "onshore", "Saudi Well"),
    # Caspian – offshore
    (41.0, 50.5, 2.0, 2.0, 600, "offshore", "Caspian Platform"),
    # West Siberia – onshore
    (61.0, 73.0, 4.0, 8.0, 1000, "onshore", "Siberia Well"),
    # Niger Delta – onshore + offshore
    (5.0, 5.5, 1.5, 2.0, 500, "offshore", "Niger Delta Platform"),
    (5.5, 6.5, 1.0, 1.5, 400, "onshore", "Niger Delta Well"),
    # Santos Basin (Brazil) – offshore
    (-24.5, -43.0, 2.0, 2.0, 500, "offshore", "Santos Platform"),
    # Southeast Asia – offshore
    (5.0, 108.0, 5.0, 8.0, 600, "offshore", "SE Asia Platform"),
    # Alaska North Slope – onshore
    (70.0, -150.0, 0.5, 3.0, 300, "onshore", "Alaska Well"),
    # Canada Oil Sands – onshore
    (57.0, -111.5, 1.5, 1.5, 400, "onshore", "Oil Sands Well"),
    # Libya – onshore
    (29.0, 18.0, 3.0, 4.0, 300, "onshore", "Libya Well"),
    # Argentina – Vaca Muerta
    (-38.5, -69.0, 1.0, 1.5, 300, "onshore", "Vaca Muerta Well"),
]


def _gaussian_point(center: float, spread: float) -> float:
    """Generate a point using Gaussian distribution, clamped to ±2*spread."""
    val = random.gauss(center, spread / 2)
    return round(max(center - spread, min(center + spread, val)), 5)


def _generate_fallback() -> dict:
    """Generate ~15 000 realistic well/platform points."""
    features: list[dict] = []
    fid = 0

    for (clat, clng, slat, slng, count, stype, prefix) in BASINS:
        for i in range(count):
            fid += 1
            lat = _gaussian_point(clat, slat)
            lng = _gaussian_point(clng, slng)
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lng, lat]},
                "properties": {
                    "id": f"w{fid:06d}",
                    "name": f"{prefix} #{i + 1}",
                    "type": stype,
                    "lat": lat,
                    "lng": lng,
                },
            })

    random.shuffle(features)
    print(f"[fallback] Generated {len(features)} facilities across {len(BASINS)} basins")
    return {"type": "FeatureCollection", "features": features}


# ── Normalisation ────────────────────────────────────────────────

def _normalise(data: dict) -> dict:
    """
    Normalise features to the strict schema:
    properties: { id, name, type, lat, lng }
    """
    out_features: list[dict] = []
    for i, feat in enumerate(data.get("features", [])):
        geom = feat.get("geometry")
        if not geom or geom.get("type") != "Point":
            continue
        coords = geom.get("coordinates", [])
        if len(coords) < 2:
            continue

        lng, lat = float(coords[0]), float(coords[1])
        props = feat.get("properties") or {}

        name = (
            props.get("name") or props.get("NAME") or props.get("Name")
            or props.get("field_name") or props.get("plant_name")
            or f"Facility #{i + 1}"
        )

        stype = props.get("type") or props.get("TYPE") or props.get("status") or "onshore"
        if stype not in ("offshore", "onshore"):
            stype = "offshore" if abs(lat) < 1 or "offshore" in stype.lower() else "onshore"

        fid = props.get("id") or props.get("ID") or f"f{i:06d}"

        out_features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(lng, 5), round(lat, 5)]},
            "properties": {
                "id": str(fid),
                "name": str(name),
                "type": stype,
                "lat": round(lat, 5),
                "lng": round(lng, 5),
            },
        })

    return {"type": "FeatureCollection", "features": out_features}


# ── Main ─────────────────────────────────────────────────────────

def main() -> None:
    print(f"[etl] Output: {OUTPUT}")

    # 1. Try remote
    data = _try_remote_download()

    # 2. Fallback
    if data is None:
        print("[etl] Remote sources unavailable, generating fallback dataset")
        data = _generate_fallback()

    # 3. Normalise
    data = _normalise(data)
    print(f"[etl] Final dataset: {len(data['features'])} facilities")

    # 4. Write
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT, "w", encoding="utf-8") as f:
        json.dump(data, f, separators=(",", ":"))

    size_mb = OUTPUT.stat().st_size / (1024 * 1024)
    print(f"[etl] Written to {OUTPUT} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
