"""
Smoke tests — verify imports and config values without hitting the database.
Marked as unit so they run without a live PostgreSQL connection.
"""

import pytest
from app.core.config import settings
from app.models import (
    Position,
    Transaction,
    ScreenHistory,
    ConvictionHistory,
    FairValueHistory,
)
from app.core.database import Base


@pytest.mark.unit
def test_all_models_registered() -> None:
    tables = set(Base.metadata.tables.keys())
    assert tables == {
        "positions",
        "transactions",
        "screen_history",
        "conviction_history",
        "fair_value_history",
    }


@pytest.mark.unit
def test_shariah_thresholds_match_spec() -> None:
    assert settings.SHARIAH_DEBT_TO_MARKETCAP_MAX == 0.30
    assert settings.SHARIAH_CASH_TO_MARKETCAP_MAX == 0.30
    assert settings.SHARIAH_IMPURE_REVENUE_MAX == 0.03
    assert settings.SHARIAH_INTEREST_INCOME_MAX == 0.03
    assert settings.SHARIAH_RECEIVABLES_TO_ASSETS_MAX == 0.45


@pytest.mark.unit
def test_dca_env_vars_match_spec() -> None:
    assert settings.STOP_LOSS_ATR_MULTIPLIER == 2.0
    assert settings.DCA_MONTHLY_BUDGET_EUR == 200.0
    assert settings.DCA_MIN_TRANCHE_EUR == 25.0
    assert settings.DCA_PARTIAL_SELL_PCT_AT_2X == 0.33
    assert settings.PORTFOLIO_TARGET_POSITIONS_MIN == 6
    assert settings.PORTFOLIO_TARGET_POSITIONS_MAX == 10
    assert settings.CONCENTRATION_HHI_ALERT_THRESHOLD == 0.40
    assert settings.SECTOR_MAX_WEIGHT_ALERT == 0.35
    assert settings.FOREIGN_EXPOSURE_ALERT == 0.70
