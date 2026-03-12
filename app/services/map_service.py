"""Static infrastructure points for the global map overlay."""

from __future__ import annotations

INFRASTRUCTURE_POINTS: list[dict] = [
    # ── Energy: Oil & Gas platforms / hubs ──────────────────────────
    {"id": "e01", "name": "Brent Field (North Sea)",       "category": "energy", "lat": 61.05, "lng": 1.72},
    {"id": "e02", "name": "Ekofisk (North Sea)",           "category": "energy", "lat": 56.54, "lng": 3.21},
    {"id": "e03", "name": "Cushing Hub (Oklahoma)",        "category": "energy", "lat": 35.98, "lng": -96.77},
    {"id": "e04", "name": "Gulf of Mexico Hub",            "category": "energy", "lat": 27.80, "lng": -90.50},
    {"id": "e05", "name": "Ras Tanura (Saudi Arabia)",     "category": "energy", "lat": 26.64, "lng": 50.16},
    {"id": "e06", "name": "Kharg Island (Iran)",           "category": "energy", "lat": 29.23, "lng": 50.31},
    {"id": "e07", "name": "Jamnagar Refinery (India)",     "category": "energy", "lat": 22.47, "lng": 70.00},
    {"id": "e08", "name": "Jurong Island (Singapore)",     "category": "energy", "lat": 1.27,  "lng": 103.70},
    {"id": "e09", "name": "Daqing Oilfield (China)",       "category": "energy", "lat": 46.58, "lng": 125.01},
    {"id": "e10", "name": "Permian Basin (Texas)",         "category": "energy", "lat": 31.97, "lng": -102.08},
    {"id": "e11", "name": "Ghawar Field (Saudi Arabia)",   "category": "energy", "lat": 25.38, "lng": 49.40},
    {"id": "e12", "name": "Kashagan (Kazakhstan)",         "category": "energy", "lat": 46.24, "lng": 51.53},
    # ── Energy: LNG terminals ───────────────────────────────────────
    {"id": "e13", "name": "Sabine Pass LNG (Louisiana)",   "category": "energy", "lat": 29.74, "lng": -93.86},
    {"id": "e14", "name": "Ras Laffan LNG (Qatar)",        "category": "energy", "lat": 25.91, "lng": 51.53},
    {"id": "e15", "name": "Ichthys LNG (Australia)",       "category": "energy", "lat": -12.56,"lng": 130.78},
    {"id": "e16", "name": "Hammerfest LNG (Norway)",       "category": "energy", "lat": 70.60, "lng": 23.63},
    {"id": "e17", "name": "Yamal LNG (Russia)",            "category": "energy", "lat": 71.27, "lng": 72.91},
    {"id": "e18", "name": "Bonny Island LNG (Nigeria)",    "category": "energy", "lat": 4.42,  "lng": 7.15},
    # ── Logistics: Major container / trade ports ────────────────────
    {"id": "l01", "name": "Port of Rotterdam",             "category": "logistics", "lat": 51.95, "lng": 4.14},
    {"id": "l02", "name": "Port of Shanghai",              "category": "logistics", "lat": 31.37, "lng": 121.62},
    {"id": "l03", "name": "Port of Singapore",             "category": "logistics", "lat": 1.26,  "lng": 103.84},
    {"id": "l04", "name": "Port of Los Angeles",           "category": "logistics", "lat": 33.74, "lng": -118.27},
    {"id": "l05", "name": "Port of Busan (South Korea)",   "category": "logistics", "lat": 35.10, "lng": 129.04},
    {"id": "l06", "name": "Port of Jebel Ali (Dubai)",     "category": "logistics", "lat": 25.01, "lng": 55.06},
    {"id": "l07", "name": "Port of Hamburg",               "category": "logistics", "lat": 53.53, "lng": 9.97},
    {"id": "l08", "name": "Port of Antwerp-Bruges",        "category": "logistics", "lat": 51.27, "lng": 4.34},
    {"id": "l09", "name": "Port of Shenzhen",              "category": "logistics", "lat": 22.48, "lng": 113.90},
    {"id": "l10", "name": "Port of Ningbo-Zhoushan",       "category": "logistics", "lat": 29.95, "lng": 121.85},
    {"id": "l11", "name": "Port of Tanger Med (Morocco)",  "category": "logistics", "lat": 35.89, "lng": -5.50},
    {"id": "l12", "name": "Port of Santos (Brazil)",       "category": "logistics", "lat": -23.96,"lng": -46.30},
    {"id": "l13", "name": "Port of Colombo (Sri Lanka)",   "category": "logistics", "lat": 6.94,  "lng": 79.85},
    {"id": "l14", "name": "Port of Piraeus (Greece)",      "category": "logistics", "lat": 37.94, "lng": 23.63},
    {"id": "l15", "name": "Port of Long Beach",            "category": "logistics", "lat": 33.76, "lng": -118.19},
    {"id": "l16", "name": "Port of Durban (South Africa)", "category": "logistics", "lat": -29.87,"lng": 31.03},
    # ── Logistics: Strategic chokepoints ────────────────────────────
    {"id": "l17", "name": "Suez Canal",                    "category": "logistics", "lat": 30.46, "lng": 32.35},
    {"id": "l18", "name": "Panama Canal",                  "category": "logistics", "lat": 9.08,  "lng": -79.68},
    {"id": "l19", "name": "Strait of Malacca",             "category": "logistics", "lat": 2.50,  "lng": 101.20},
    {"id": "l20", "name": "Strait of Hormuz",              "category": "logistics", "lat": 26.57, "lng": 56.25},
    {"id": "l21", "name": "Bab el-Mandeb",                 "category": "logistics", "lat": 12.58, "lng": 43.33},
    {"id": "l22", "name": "Cape of Good Hope",             "category": "logistics", "lat": -34.36,"lng": 18.47},
]


def get_infrastructure_points() -> list[dict]:
    """Return the full static list of infrastructure points."""
    return INFRASTRUCTURE_POINTS
