from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    app_name: str = "FinTerminal API"
    app_version: str = "1.0.0"
    app_env: str = "development"
    log_level: str = "INFO"

    # API Keys
    anthropic_api_key: str = ""
    alpha_vantage_api_key: str = ""

    # CORS
    cors_origins: str = "http://localhost:3000,http://localhost:5173"

    # Server
    port: int = 8000

    # Rate Limiting
    rate_limit_per_minute: int = 30

    # Claude Model
    claude_model: str = "claude-sonnet-4-20250514"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
