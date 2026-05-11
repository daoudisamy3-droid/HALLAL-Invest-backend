"""
Central configuration — all tuneable thresholds and environment variables.

Sources : spec §5.1 (ShariahCustomThresholds), §6.3 (DCA engine), §10.1 (env vars).
"""

from pydantic_settings import BaseSettings, SettingsConfigDict
from typing import Optional


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/hallal_invest"
    DATABASE_POOL_SIZE: int = 10
    DATABASE_MAX_OVERFLOW: int = 20

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = "Hallal Invest API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"
    ALLOWED_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # ── Shariah screening thresholds  §5.1 (ShariahCustomThresholds) ─────────
    # Stricter than AAOIFI standard — spec §5.1 justification :
    # "debt/MC ≤ 30% (au lieu de 33% classique) : tolérance zéro sur Riba structurel"
    # "impure_revenue ≤ 3% (au lieu de 5%) : approche Khatakhatay & Nizar 2007"
    SHARIAH_DEBT_TO_MARKETCAP_MAX: float = 0.30       # §5.1 + §10.1
    SHARIAH_CASH_TO_MARKETCAP_MAX: float = 0.30       # §5.1 + §10.1
    SHARIAH_IMPURE_REVENUE_MAX: float = 0.03          # §5.1 + §10.1
    SHARIAH_INTEREST_INCOME_MAX: float = 0.03         # §5.1 + §10.1
    SHARIAH_RECEIVABLES_TO_ASSETS_MAX: float = 0.45   # §5.1

    # ── DCA engine  §6.3 + §10.1 ─────────────────────────────────────────────
    # price/FV ratio thresholds driving buy-size matrix (§6.3)
    # 0.95 → 100 % budget | 1.05 → 70 % | 1.50 → 0 % | 2.0 → partial sell
    STOP_LOSS_ATR_MULTIPLIER: float = 2.0             # §10.1
    DCA_MONTHLY_BUDGET_EUR: float = 200.0             # §10.1
    DCA_MIN_TRANCHE_EUR: float = 25.0                 # §4.4.4 consolidate_tranches + §10.1
    DCA_PARTIAL_SELL_PCT_AT_2X: float = 0.33          # §6.3 + §10.1

    # ── Portfolio risk thresholds  §10.1 ─────────────────────────────────────
    PORTFOLIO_TARGET_POSITIONS_MIN: int = 6           # §10.1
    PORTFOLIO_TARGET_POSITIONS_MAX: int = 10          # §10.1
    CONCENTRATION_HHI_ALERT_THRESHOLD: float = 0.40  # §10.1
    SECTOR_MAX_WEIGHT_ALERT: float = 0.35             # §10.1
    FOREIGN_EXPOSURE_ALERT: float = 0.70              # §10.1

    # ── Anti-bot API key  §9.6 ───────────────────────────────────────────────
    # Must be a UUID — set via FINTERMINAL_API_KEY env var on Railway.
    # No default: missing value → ValidationError at startup (fail-fast).
    FINTERMINAL_API_KEY: str

    # ── External data providers ───────────────────────────────────────────────
    FMP_API_KEY: Optional[str] = None
    ALPHA_VANTAGE_KEY: Optional[str] = None
    MUSAFFA_API_KEY: Optional[str] = None


settings = Settings()
