"""Static infrastructure points and geo data for the global map overlay."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

INFRASTRUCTURE_POINTS: list[dict] = [
    # ── Energy (10): platforms, hubs, LNG terminals ──────────────────
    {"id": "e01", "name": "Brent Field (North Sea)",       "category": "energy", "lat": 61.05, "lng": 1.72},
    {"id": "e02", "name": "Cushing Hub (Oklahoma)",        "category": "energy", "lat": 35.98, "lng": -96.77},
    {"id": "e03", "name": "Gulf of Mexico Hub",            "category": "energy", "lat": 27.80, "lng": -90.50},
    {"id": "e04", "name": "Ras Tanura (Saudi Arabia)",     "category": "energy", "lat": 26.64, "lng": 50.16},
    {"id": "e05", "name": "Ghawar Field (Saudi Arabia)",   "category": "energy", "lat": 25.38, "lng": 49.40},
    {"id": "e06", "name": "Permian Basin (Texas)",         "category": "energy", "lat": 31.97, "lng": -102.08},
    {"id": "e07", "name": "Jurong Island (Singapore)",     "category": "energy", "lat": 1.27,  "lng": 103.70},
    {"id": "e08", "name": "Sabine Pass LNG (Louisiana)",   "category": "energy", "lat": 29.74, "lng": -93.86},
    {"id": "e09", "name": "Ras Laffan LNG (Qatar)",        "category": "energy", "lat": 25.91, "lng": 51.53},
    {"id": "e10", "name": "Yamal LNG (Russia)",            "category": "energy", "lat": 71.27, "lng": 72.91},
    # ── Logistics (10): major ports + chokepoints ────────────────────
    {"id": "l01", "name": "Port of Rotterdam",             "category": "logistics", "lat": 51.95, "lng": 4.14},
    {"id": "l02", "name": "Port of Shanghai",              "category": "logistics", "lat": 31.37, "lng": 121.62},
    {"id": "l03", "name": "Port of Singapore",             "category": "logistics", "lat": 1.26,  "lng": 103.84},
    {"id": "l04", "name": "Port of Los Angeles",           "category": "logistics", "lat": 33.74, "lng": -118.27},
    {"id": "l05", "name": "Port of Busan (South Korea)",   "category": "logistics", "lat": 35.10, "lng": 129.04},
    {"id": "l06", "name": "Port of Jebel Ali (Dubai)",     "category": "logistics", "lat": 25.01, "lng": 55.06},
    {"id": "l07", "name": "Suez Canal",                    "category": "logistics", "lat": 30.46, "lng": 32.35},
    {"id": "l08", "name": "Panama Canal",                  "category": "logistics", "lat": 9.08,  "lng": -79.68},
    {"id": "l09", "name": "Strait of Malacca",             "category": "logistics", "lat": 2.50,  "lng": 101.20},
    {"id": "l10", "name": "Strait of Hormuz",              "category": "logistics", "lat": 26.57, "lng": 56.25},
]


def get_infrastructure_points() -> list[dict]:
    """Return the full static list of infrastructure points."""
    return INFRASTRUCTURE_POINTS


# ── Energy chokepoints (geopolitical) ────────────────────────────

ENERGY_CHOKEPOINTS: list[dict] = [
    {
        "id": "ck01", "name": "Détroit d'Ormuz", "lat": 26.56, "lng": 56.25,
        "desc": "20% du pétrole mondial",
        "volume": "21M barils/j", "impact": "20% conso mondiale",
        "proxy_ticker": "FRO", "proxy_name": "Frontline (Tankers)",
    },
    {
        "id": "ck02", "name": "Détroit de Malacca", "lat": 1.43, "lng": 102.89,
        "desc": "Hub Asie",
        "volume": "16M barils/j", "impact": "Hub Asie",
        "proxy_ticker": "CEO", "proxy_name": "CNOOC",
    },
    {
        "id": "ck03", "name": "Canal de Suez", "lat": 30.58, "lng": 32.34,
        "desc": "Route Europe-Asie",
        "volume": "9M barils/j", "impact": "12% commerce mondial",
        "proxy_ticker": "ZIM", "proxy_name": "ZIM Shipping",
    },
    {
        "id": "ck04", "name": "Canal de Panama", "lat": 9.14, "lng": -79.72,
        "desc": "Route Amériques",
        "volume": "5% commerce global", "impact": "Route GNL (Gaz)",
        "proxy_ticker": "LNG", "proxy_name": "Cheniere Energy",
    },
    {
        "id": "ck05", "name": "Bab el-Mandeb", "lat": 12.58, "lng": 43.33,
        "desc": "Mer Rouge",
        "volume": "9M barils/j", "impact": "12% commerce mondial",
        "proxy_ticker": "ZIM", "proxy_name": "ZIM Shipping",
    },
    {
        "id": "ck06", "name": "Détroit du Bosphore", "lat": 41.22, "lng": 29.11,
        "desc": "Exportations Mer Noire/Caspienne",
        "volume": "3M barils/j", "impact": "Exportations Mer Noire/Caspienne",
        "proxy_ticker": "TNK", "proxy_name": "Teekay Tankers",
    },
    {
        "id": "ck07", "name": "Détroits Danois", "lat": 55.30, "lng": 11.00,
        "desc": "Exportations Baltique/Russie",
        "volume": "3.2M barils/j", "impact": "Exportations Baltique/Russie",
        "proxy_ticker": "TNK", "proxy_name": "Teekay Tankers",
    },
]


def get_energy_chokepoints() -> list[dict]:
    """Return the 7 major energy chokepoints."""
    return ENERGY_CHOKEPOINTS


# ── Piracy risk zones (static GeoJSON) ───────────────────────────

PIRACY_RISK_GEOJSON: dict = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "properties": {
                "id": "pz01",
                "name": "Golfe d'Aden",
                "risk_level": "High",
                "desc": "Corridor Somalie – Yémen, piraterie historique",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [43.0, 11.0], [51.0, 11.0], [51.0, 15.0],
                    [43.0, 15.0], [43.0, 11.0],
                ]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "id": "pz02",
                "name": "Détroit de Malacca",
                "risk_level": "High",
                "desc": "Zone de piraterie active, trafic maritime dense",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [99.0, -1.0], [105.0, -1.0], [105.0, 4.0],
                    [99.0, 4.0], [99.0, -1.0],
                ]],
            },
        },
        {
            "type": "Feature",
            "properties": {
                "id": "pz03",
                "name": "Golfe de Guinée",
                "risk_level": "High",
                "desc": "Côtes Nigeria – Cameroun – Ghana, enlèvements et vols",
            },
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [-5.0, 0.0], [10.0, 0.0], [10.0, 7.0],
                    [-5.0, 7.0], [-5.0, 0.0],
                ]],
            },
        },
    ],
}


def get_piracy_risk() -> dict:
    """Return a lightweight GeoJSON FeatureCollection of piracy risk zones."""
    return PIRACY_RISK_GEOJSON


# ── Oil production sites (GeoJSON from local file) ───────────────

_EMPTY_FEATURE_COLLECTION: dict[str, Any] = {"type": "FeatureCollection", "features": []}

_PRODUCTION_GEOJSON_PATH = Path(__file__).resolve().parent.parent / "data" / "global_oil_production.geojson"

# In-memory cache – loaded once, never expires (static file).
_production_cache: dict[str, Any] | None = None


def get_production_geojson() -> dict[str, Any]:
    """
    Load and cache the global oil production GeoJSON.

    Resilience rules:
    - File missing / unreadable → empty FeatureCollection + log warning
    - Corrupt JSON → empty FeatureCollection + log error
    - Never raises, never returns 500.
    """
    global _production_cache
    if _production_cache is not None:
        return _production_cache

    try:
        raw = _PRODUCTION_GEOJSON_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get("type") != "FeatureCollection":
            logger.error("production geojson: invalid structure, returning empty")
            _production_cache = _EMPTY_FEATURE_COLLECTION
            return _production_cache
        _production_cache = data
        return _production_cache
    except FileNotFoundError:
        logger.warning("production geojson not found at %s", _PRODUCTION_GEOJSON_PATH)
        _production_cache = _EMPTY_FEATURE_COLLECTION
        return _production_cache
    except json.JSONDecodeError as exc:
        logger.error("production geojson corrupt: %s", exc)
        _production_cache = _EMPTY_FEATURE_COLLECTION
        return _production_cache
    except Exception as exc:
        logger.error("production geojson unexpected error: %s", exc)
        _production_cache = _EMPTY_FEATURE_COLLECTION
        return _production_cache
