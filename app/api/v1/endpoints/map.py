from fastapi import APIRouter

from app.services.map_service import get_infrastructure_points, get_energy_chokepoints, get_piracy_risk

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
