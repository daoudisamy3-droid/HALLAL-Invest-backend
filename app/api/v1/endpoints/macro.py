from fastapi import APIRouter, Depends

from app.core.security import rate_limit_dependency
from app.services.macro_service import (
    get_world_indices,
    get_comparison_data,
    get_commodities_data,
    get_commodities_comparison,
    get_risk_data,
    get_risk_comparison,
    get_energy_data,
    get_energy_comparison,
    get_energy_chart,
    get_energy_table,
    get_logistics_data,
    get_logistics_comparison,
)

router = APIRouter()


@router.get(
    "/macro/indices",
    summary="World market indices",
    description="S&P 500, Nasdaq 100, CAC 40, DAX, Nikkei 225 — price, change %, and market open status.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def world_indices():
    return await get_world_indices()


@router.get(
    "/macro/comparison",
    summary="24h Follow-the-Sun comparison (LineChart-ready)",
    description="Flat array of {time, US500, NDX100, FR40, DE40, JP225} on a UTC 24h axis. Each index normalised to 0% at its own session open. Asia → Europe → USA.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def comparison():
    return await get_comparison_data()


@router.get(
    "/macro/commodities",
    summary="Commodities snapshot",
    description="Gold, Brent Oil, Natural Gas, Silver — price, change %, and market open status.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def commodities():
    return await get_commodities_data()


@router.get(
    "/macro/commodities/comparison",
    summary="Commodities 24h comparison (LineChart-ready)",
    description="Flat array of {time, GOLD, BRENT, NATGAS, SILVER} on a UTC 24h axis. Base 0% at first point.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def commodities_comparison():
    return await get_commodities_comparison()


@router.get(
    "/macro/risk",
    summary="Systemic risk indicators",
    description="VIX (volatility) and US 10Y yield — raw values, daily change, and market status.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def risk():
    return await get_risk_data()


@router.get(
    "/macro/risk/comparison",
    summary="Risk indicators 24h comparison (LineChart-ready)",
    description="Flat array of {time, VIX, US10Y} on a UTC 24h axis. Base 0% at first data point.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def risk_comparison():
    return await get_risk_comparison()


@router.get(
    "/macro/energy",
    summary="Energy sector snapshot",
    description="WTI Crude Oil, Heating Oil, RBOB Gasoline — price, change %, and market status.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def energy():
    return await get_energy_data()


@router.get(
    "/macro/energy/comparison",
    summary="Energy 24h comparison (LineChart-ready)",
    description="Flat array of {time, WTI, HEAT_OIL, GASOLINE} on a UTC 24h axis. Base 0% at first data point.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def energy_comparison():
    return await get_energy_comparison()


@router.get(
    "/macro/energy/chart",
    summary="Energy correlation chart (WTI vs XLE)",
    description="24h base-0% normalised series for WTI Crude and Energy Select ETF (XLE). Format: [{time, WTI, XLE}].",
    dependencies=[Depends(rate_limit_dependency)],
)
async def energy_chart():
    return await get_energy_chart()


@router.get(
    "/macro/energy/table",
    summary="Energy live table",
    description="WTI, Brent, Natural Gas, Heating Oil — price, change %, day high/low, and market status.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def energy_table():
    return await get_energy_table()


@router.get(
    "/macro/logistics",
    summary="Logistics sector snapshot",
    description="Transport ETF (IYT), FedEx, Maersk (ADR), ZIM Shipping — price, change %, and market status.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def logistics():
    return await get_logistics_data()


@router.get(
    "/macro/logistics/comparison",
    summary="Logistics 24h comparison (LineChart-ready)",
    description="Flat array of {time, IYT, FDX, MAERSK, ZIM} on a UTC 24h axis. Base 0% at first data point.",
    dependencies=[Depends(rate_limit_dependency)],
)
async def logistics_comparison():
    return await get_logistics_comparison()
