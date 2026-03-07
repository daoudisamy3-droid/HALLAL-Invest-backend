from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "HALLAL Invest API"
    app_version: str = "2.0.0"
    app_env: str = "development"
    log_level: str = "INFO"

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:5173,https://hallal-invest-frontend-production.up.railway.app"

    # Server
    port: int = 8000

    # Rate Limiting
    rate_limit_per_minute: int = 30

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
