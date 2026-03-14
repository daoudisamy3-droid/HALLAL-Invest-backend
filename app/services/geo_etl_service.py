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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "layers"

_EMPTY_FC: dict[str, Any] = {"type": "FeatureCollection", "features": []}

# Fallback cap_kbpd by layer category
_CAP_DEFAULTS: dict[str, int] = {
    "oil_gas_fields": 100,
    "pipelines": 500,
    "refineries_lng": 200,
    "offshore_platforms": 50,
}

# 24-hour in-memory cache
_CACHE_TTL = 86_400  # seconds
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


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


def _cache_get(key: str) -> dict[str, Any] | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, data = entry
    if (datetime.now(timezone.utc).timestamp() - ts) > _CACHE_TTL:
        return None
    return data


def _cache_set(key: str, data: dict[str, Any]) -> None:
    _cache[key] = (datetime.now(timezone.utc).timestamp(), data)


def _load_layer(layer: str) -> dict[str, Any]:
    """
    Load a GeoJSON layer from disk, minify, and cache.

    Never raises – returns empty FeatureCollection on any error.
    """
    cached = _cache_get(layer)
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

    _cache_set(layer, result)
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

_facilities_cache: dict[str, Any] | None = None


def get_facilities() -> dict[str, Any]:
    """
    Load the massive facilities GeoJSON (15 000+ wells & platforms).

    Uses permanent in-memory cache (static data). Never raises.
    """
    global _facilities_cache
    if _facilities_cache is not None:
        return _facilities_cache

    path = _DATA_DIR / "facilities.geojson"
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict) and data.get("type") == "FeatureCollection":
            _facilities_cache = data
            logger.info("facilities: loaded %d features (%.1f MB)",
                        len(data["features"]), len(raw) / 1_048_576)
            return _facilities_cache
        logger.error("facilities: invalid GeoJSON structure")
    except FileNotFoundError:
        logger.warning("facilities: file not found at %s – run scripts/fetch_wells_data.py", path)
    except json.JSONDecodeError as exc:
        logger.error("facilities: corrupt JSON – %s", exc)
    except Exception as exc:
        logger.error("facilities: unexpected error – %s", exc)

    _facilities_cache = _EMPTY_FC
    return _facilities_cache
