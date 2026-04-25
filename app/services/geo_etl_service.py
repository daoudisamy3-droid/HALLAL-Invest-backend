"""
GeoJSON ETL service – ingest, minify, cache, and serve 4 map layers.

Layers: oil_gas_fields, pipelines, refineries_lng, offshore_platforms.

Data flow:
  1. Try loading from app/data/layers/<name>.geojson
  2. Minify: strip all properties except {id, name, cap_kbpd, type}
  3. Apply cap_kbpd fallback by category
  4. Cache in memory for 24h (static infrastructure)
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.cache import cache_get as _central_cache_get, cache_set as _central_cache_set

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "layers"

_EMPTY_FC: dict[str, Any] = {"type": "FeatureCollection", "features": []}

_GEO_CACHE_NS = "geo"
_GEO_CACHE_TTL = 86_400  # 24 hours

# Fallback cap_kbpd by layer category
_CAP_DEFAULTS: dict[str, int] = {
    "oil_gas_fields": 100,
    "pipelines": 500,
    "refineries_lng": 200,
    "offshore_platforms": 50,
}


# ── Minification ────────────────────────────────────────────────


def _minify_feature(feat: dict, layer: str) -> dict[str, Any] | None:
    """
    Strip a raw GeoJSON feature to the golden-source schema.

    Returns None if geometry is missing (skip the feature).
    """
    geom = feat.get("geometry")
    if not geom:
        return None

    raw_props = feat.get("properties") or {}

    # Resolve id: try several common field names
    fid = (
        raw_props.get("id")
        or raw_props.get("ID")
        or raw_props.get("gid")
        or raw_props.get("ogc_fid")
        or feat.get("id")
        or ""
    )

    name = (
        raw_props.get("name")
        or raw_props.get("NAME")
        or raw_props.get("Name")
        or raw_props.get("field_name")
        or raw_props.get("plant_name")
        or "Unknown"
    )

    cap = raw_props.get("cap_kbpd") or raw_props.get("capacity_kbpd")
    if cap is None:
        cap = _CAP_DEFAULTS.get(layer, 100)
    else:
        try:
            cap = int(cap)
        except (ValueError, TypeError):
            cap = _CAP_DEFAULTS.get(layer, 100)

    site_type = (
        raw_props.get("type")
        or raw_props.get("TYPE")
        or raw_props.get("facility_type")
        or layer
    )

    return {
        "type": "Feature",
        "geometry": geom,
        "properties": {
            "id": str(fid),
            "name": str(name),
            "cap_kbpd": cap,
            "type": str(site_type),
        },
    }


def _minify_collection(data: dict, layer: str) -> dict[str, Any]:
    """Minify an entire FeatureCollection."""
    features = []
    for feat in data.get("features", []):
        minified = _minify_feature(feat, layer)
        if minified is not None:
            features.append(minified)
    return {"type": "FeatureCollection", "features": features}


# ── Load + cache ────────────────────────────────────────────────


def _load_layer(layer: str) -> dict[str, Any]:
    """
    Load a GeoJSON layer from disk, minify, and cache.

    Never raises – returns empty FeatureCollection on any error.
    """
    cached = _central_cache_get(_GEO_CACHE_NS, layer)
    if cached is not None:
        return cached

    path = _DATA_DIR / f"{layer}.geojson"
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            logger.error("layer %s: invalid GeoJSON structure", layer)
            result = _EMPTY_FC
        else:
            result = _minify_collection(data, layer)
            logger.info("layer %s: loaded & minified %d features", layer, len(result["features"]))
    except FileNotFoundError:
        logger.warning("layer %s: file not found at %s", layer, path)
        result = _EMPTY_FC
    except json.JSONDecodeError as exc:
        logger.error("layer %s: corrupt JSON – %s", layer, exc)
        result = _EMPTY_FC
    except Exception as exc:
        logger.error("layer %s: unexpected error – %s", layer, exc)
        result = _EMPTY_FC

    _central_cache_set(_GEO_CACHE_NS, layer, result, ttl=_GEO_CACHE_TTL)
    return result


# ── Public API ──────────────────────────────────────────────────


def get_fields() -> dict[str, Any]:
    """Oil & gas fields layer."""
    return _load_layer("oil_gas_fields")


def get_pipelines() -> dict[str, Any]:
    """Pipelines layer."""
    return _load_layer("pipelines")


def get_refineries() -> dict[str, Any]:
    """Refineries & LNG terminals layer."""
    return _load_layer("refineries_lng")


def get_offshore() -> dict[str, Any]:
    """Offshore platforms layer."""
    return _load_layer("offshore_platforms")


# ── Facilities (massive unified layer, 15k+ points) ─────────────
#
# Priority order:
#   1. OSM real data  (app/data/osm_energy_facilities.json)
#   2. Generated data (app/data/layers/facilities.geojson)
#   3. Empty FeatureCollection (never 500)

_OSM_PATH = Path(__file__).resolve().parent.parent / "data" / "osm_energy_facilities.json"
_GENERATED_PATH = _DATA_DIR / "facilities.geojson"

_facilities_cache: dict[str, Any] | None = None
_fetch_triggered = False

# ── Indestructible hardcoded fallback ────────────────────────────
# Survival data: if OSM is empty/missing AND the generated dataset
# is missing, we MUST still return real points so the frontend map
# can render. These 4 points represent the world's most iconic
# oil & gas sites and are guaranteed to always be available.
_HARDCODED_FACILITIES: list[dict[str, Any]] = [
    {"lat": 26.0, "lon": 49.0, "cap_kbpd": 5000, "type": "onshore", "name": "Ghawar"},
    {"lat": 31.0, "lon": -102.0, "cap_kbpd": 4000, "type": "onshore", "name": "Permian"},
    {"lat": 56.0, "lon": 3.0, "cap_kbpd": 1500, "type": "offshore", "name": "North Sea Alpha"},
    {"lat": 28.0, "lon": -90.0, "cap_kbpd": 2000, "type": "offshore", "name": "GOM Deepwater"},
]


def _hardcoded_fallback_fc() -> dict[str, Any]:
    """
    Build a GeoJSON FeatureCollection from the hardcoded 4-point survival
    dataset. Schema matches the existing facilities endpoint contract:
    properties: {id, name, type, cap_kbpd, lat, lng}.
    """
    features = []
    for i, site in enumerate(_HARDCODED_FACILITIES):
        lat = site["lat"]
        lon = site["lon"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lon, lat]},
            "properties": {
                "id": f"hc_{i + 1}",
                "name": site["name"],
                "type": site["type"],
                "cap_kbpd": site["cap_kbpd"],
                "lat": lat,
                "lng": lon,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _osm_to_geojson(data: dict) -> dict[str, Any]:
    """Convert our OSM JSON format to standard GeoJSON FeatureCollection."""
    features = []
    for i, f in enumerate(data.get("features", [])):
        lat = f.get("lat")
        lng = f.get("lng")
        if lat is None or lng is None:
            continue
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [lng, lat]},
            "properties": {
                "id": str(f.get("osm_id", f"osm_{i}")),
                "name": f.get("name") or f"OSM {f.get('type', 'facility')} #{i + 1}",
                "type": f.get("type", "onshore"),
                "lat": lat,
                "lng": lng,
            },
        })
    return {"type": "FeatureCollection", "features": features}


def _try_load_osm() -> dict[str, Any] | None:
    """Try loading real OSM data. Returns GeoJSON or None if empty/missing."""
    try:
        if not _OSM_PATH.exists():
            return None
        raw = _OSM_PATH.read_text(encoding="utf-8")
        if not raw.strip():
            logger.info("osm data file empty, skipping")
            return None
        data = json.loads(raw)
        features_raw = data.get("features") or []
        total = data.get("total", len(features_raw))
        if total <= 0 or not features_raw:
            logger.info("osm data has 0 features, skipping")
            return None
        if total < 100:
            logger.info("osm data too small (%d features), skipping", total)
            return None
        geojson = _osm_to_geojson(data)
        if not geojson.get("features"):
            logger.info("osm data produced 0 valid features, skipping")
            return None
        logger.info("facilities: loaded %d real OSM features", len(geojson["features"]))
        return geojson
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("osm data error: %s", exc)
        return None


def _try_load_generated() -> dict[str, Any] | None:
    """Try loading generated fallback data. Returns None if empty/missing."""
    try:
        if not _GENERATED_PATH.exists():
            return None
        raw = _GENERATED_PATH.read_text(encoding="utf-8")
        if not raw.strip():
            return None
        data = json.loads(raw)
        if (
            isinstance(data, dict)
            and data.get("type") == "FeatureCollection"
            and data.get("features")
        ):
            logger.info("facilities: loaded %d generated features (%.1f MB)",
                        len(data["features"]), len(raw) / 1_048_576)
            return data
    except FileNotFoundError:
        pass
    except Exception as exc:
        logger.warning("generated facilities error: %s", exc)
    return None


def _trigger_background_fetch() -> None:
    """Trigger the OSM fetch script in a background subprocess."""
    global _fetch_triggered
    if _fetch_triggered:
        return
    _fetch_triggered = True

    import subprocess
    script = Path(__file__).resolve().parent.parent.parent / "scripts" / "fetch_osm_energy.py"
    if not script.exists():
        logger.warning("fetch script not found at %s", script)
        return

    logger.info("triggering background OSM fetch: %s", script)
    try:
        subprocess.Popen(
            [sys.executable, str(script)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.warning("background fetch failed to start: %s", exc)


def get_facilities() -> dict[str, Any]:
    """
    Load facilities data with an indestructible fallback chain:
      1. OSM real data     (app/data/osm_energy_facilities.json)
      2. Generated dataset (app/data/layers/facilities.geojson)
      3. Hardcoded 4-point survival fallback (always works)

    The hardcoded fallback is NEVER cached so a later successful load
    (e.g. after background OSM fetch completes) can take over. Never
    returns an empty FeatureCollection and never raises.
    """
    global _facilities_cache
    if _facilities_cache is not None:
        return _facilities_cache

    # 1. Try real OSM data
    result = _try_load_osm()

    # 2. Fallback to generated dataset
    if result is None:
        result = _try_load_generated()

    # 3. Both missing/empty: return indestructible hardcoded fallback.
    #    Trigger a background OSM fetch so the next call can upgrade,
    #    but do NOT cache the hardcoded result.
    if result is None or not result.get("features"):
        logger.warning(
            "facilities: no real data available, returning hardcoded "
            "survival fallback (%d points) – run: python scripts/fetch_osm_energy.py",
            len(_HARDCODED_FACILITIES),
        )
        _trigger_background_fetch()
        return _hardcoded_fallback_fc()

    _facilities_cache = result
    return _facilities_cache
