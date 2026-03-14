from fastapi import APIRouter

from app.services.map_service import get_infrastructure_points, get_energy_chokepoints

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
