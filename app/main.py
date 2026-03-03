from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.core.logging import logger
from app.api.v1.router import api_router


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "FinTerminal API — Bloomberg-style financial analysis with "
            "Shariah compliance screening and AI-powered verdicts."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Routes
    app.include_router(api_router)

    @app.get("/health", tags=["System"])
    async def health_check():
        return {"status": "ok", "version": settings.app_version}

    logger.info(
        "%s v%s started (env=%s)", settings.app_name, settings.app_version, settings.app_env
    )

    return app


app = create_app()
