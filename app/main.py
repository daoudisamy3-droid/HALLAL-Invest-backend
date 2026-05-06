from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from app.core.config import get_settings
from app.core.logging import logger
from app.api.v1.router import api_router


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="HALLAL Invest API — Stock ticker validation and price data.",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS – fixed list (localhost dev + production frontend domain)
    # + regex scoped to Hallal-Invest Railway deployments (production,
    # staging, preview) — NOT a wildcard *.up.railway.app.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_origin_regex=r"https://hallal-invest-frontend(-[\w-]+)?\.up\.railway\.app",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # GZip compression (responses > 500 bytes)
    app.add_middleware(GZipMiddleware, minimum_size=500)

    # Routes
    app.include_router(api_router)

    @app.get("/health", tags=["System"])
    async def health_check():
        return {"status": "ok", "version": settings.app_version}

    # ── API key detection at startup ─────────────────────────────
    import os
    gemini_key = settings.gemini_api_key or os.getenv("GEMINI_API_KEY", "")
    fmp_key = settings.fmp_api_key or os.getenv("FMP_API_KEY", "")
    logger.info(
        "API KEYS CHECK: GEMINI_API_KEY=%s, FMP_API_KEY=%s",
        ("DETECTED" if gemini_key else "MISSING"),
        ("DETECTED" if fmp_key else "MISSING"),
    )

    logger.info(
        "%s v%s started (env=%s)", settings.app_name, settings.app_version, settings.app_env
    )

    return app


app = create_app()
