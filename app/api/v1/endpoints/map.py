from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.services.map_service import (
    get_infrastructure_points,
    get_energy_chokepoints,
    get_piracy_risk,
    get_piracy_zones,
    get_production_geojson,
    get_massive_production,
)

router = APIRouter()


@router.get(
    "/map/infrastructure",
    summary="Global infrastructure points",
    description="20 energy & logistics points (platforms, LNG terminals, major ports, chokepoints) for the map overlay.",
)
async def infrastructure():
    return get_infrastructure_points()


@router.get(
    "/map/chokepoints",
    summary="Energy chokepoints",
    description="5 major geopolitical energy chokepoints (Hormuz, Malacca, Suez, Panama, Bab el-Mandeb).",
)
async def chokepoints():
    return get_energy_chokepoints()


@router.get(
    "/map/piracy-risk",
    summary="Piracy risk zones (GeoJSON)",
    description="Lightweight GeoJSON FeatureCollection with high-risk piracy polygons: Gulf of Aden, Strait of Malacca, Gulf of Guinea.",
)
async def piracy_risk():
    return get_piracy_risk()


@router.get(
    "/map/production",
    summary="Global oil production sites (GeoJSON)",
    description="GeoJSON FeatureCollection of ~30 major oil production sites with name, type (offshore/onshore), and volume_bpd.",
)
async def production():
    data = get_production_geojson()
    return JSONResponse(
        content=data,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get(
    "/map/piracy-zones",
    summary="Piracy zones (native GeoJSON, 4 polygons)",
    description="GeoJSON FeatureCollection: Gulf of Aden, Gulf of Guinea, Strait of Malacca, Red Sea. Each with risk_level: High.",
)
async def piracy_zones():
    return get_piracy_zones()


@router.get(
    "/map/production/massive",
    summary="Massive oil production dataset (GeoJSON, 20k+ points)",
    description="GeoJSON FeatureCollection of global oil production sites with capacity_kbpd. GZip-compressed, 1h browser cache.",
)
async def production_massive():
    data = get_massive_production()
    return JSONResponse(
        content=data,
        headers={"Cache-Control": "public, max-age=3600"},
    )
