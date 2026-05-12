"""
Central configuration — all tuneable thresholds and environment variables.

Sources : spec §5.1 (ShariahCustomThresholds), §6.3 (DCA engine), §10.1 (env vars).
"""

from pydantic import field_validator
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

    @field_validator("DATABASE_URL")
    @classmethod
    def _ensure_asyncpg_driver(cls, v: str) -> str:
        """Normalise the URL so the app always sees the async driver prefix.

        Railway (and most managed Postgres providers) injects
        ``DATABASE_URL=postgresql://...`` by default. Our runtime stack uses
        SQLAlchemy async (``create_async_engine``), which requires an
        async-capable driver — ``postgresql+asyncpg://...``. Without this
        coercion, SQLAlchemy resolves the URL to the synchronous psycopg2
        dialect and async checkouts blow up at the first ``await
        db.execute(...)`` (mismatch sync pool ↔ async session).

        Alembic's ``env.py`` runs the reverse transformation
        (``+asyncpg`` → ``+psycopg2``) so migrations keep working.

        Idempotent: a URL already prefixed with ``postgresql+asyncpg://``
        or any other ``postgresql+<driver>://`` form passes through
        unchanged.
        """
        if v.startswith("postgresql://"):
            return "postgresql+asyncpg://" + v[len("postgresql://"):]
        return v

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = "Hallal Invest API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = False
    SECRET_KEY: str = "change-me-in-production"
    ALLOWED_ORIGINS: list[str] = [
        # Local dev
        "http://localhost:5173",
        "http://localhost:3000",
        # Production frontend (Railway). Hardcoded for V1 — single stable URL.
        # If a second deployed origin appears later (preview deploys, staging),
        # switch this to an env-var-fed list.
        "https://hallal-invest-frontend-production.up.railway.app",
    ]

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

    # ── Halal Terminal API  §3.2.1 ───────────────────────────────────────────
    # Provider that returns AAOIFI/DJIM/FTSE/MSCI/S&P screening ratios.
    # We extract raw ratios and apply our own ShariahCustomThresholds (§5.1).
    HALAL_TERMINAL_API_KEY: str  # No default — fail-fast like FINTERMINAL_API_KEY
    HALAL_TERMINAL_BASE_URL: str = "https://api.halalterminal.com"
    HALAL_TERMINAL_TIMEOUT_S: float = 10.0
    SHARIAH_SCREEN_TTL_DAYS: int = 7  # §3.2.1 cache window

    # ── SEC EDGAR  §3.2.2 ────────────────────────────────────────────────────
    # SEC requires the User-Agent header to identify the calling application
    # plus a contact email. Fail-fast on Railway if the variable is missing —
    # any SEC call without a proper UA gets a non-explicit 403.
    # Reminder (rule durcie): adding a new fail-fast var MUST be accompanied
    # by the same placeholder in .github/workflows/backend-ci.yml job-level env.
    SEC_EDGAR_USER_AGENT: str  # No default
    SEC_EDGAR_BASE_URL: str = "https://data.sec.gov"
    SEC_EDGAR_TICKER_MAP_URL: str = "https://www.sec.gov/files/company_tickers.json"
    SEC_EDGAR_TIMEOUT_S: float = 15.0
    FINANCIALS_CACHE_TTL_HOURS: int = 24  # §3.2.2 cache window

    # ── YFinance  (Step 5) ───────────────────────────────────────────────────
    # YFinance is an unofficial scraper of Yahoo Finance. No API key, but
    # Yahoo aggressively rate-limits and occasionally IP-blocks. We retry
    # transparently and fall back gracefully (None) on hard failure — see
    # docs/YFINANCE_INTEGRATION_NOTES.md.
    YFINANCE_TIMEOUT_S: float = 15.0
    YFINANCE_INFO_TTL_HOURS: int = 1     # live-ish quote: short TTL
    YFINANCE_HISTORY_TTL_HOURS: int = 24  # daily bars: stale-ok for a day

    # ── External data providers ───────────────────────────────────────────────
    FMP_API_KEY: Optional[str] = None
    ALPHA_VANTAGE_KEY: Optional[str] = None
    MUSAFFA_API_KEY: Optional[str] = None


settings = Settings()
