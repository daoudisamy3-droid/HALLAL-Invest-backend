"""Root conftest.py — pytest fixtures for the Hallal Invest backend test suite.

Spec §11.2 §0.3 — async engine + an isolated DB session per test.

Isolation strategy:
  * Engine is function-scoped. pytest-asyncio 1.x defaults to one event
    loop per test, and asyncpg connections are tied to the loop they
    were created in. A session-scoped engine triggers
    "another operation in progress" InterfaceError as soon as a
    function-scoped test reuses a connection that lived in a now-dead
    loop. Function-scope avoids that entirely.
  * Each test gets a fresh AsyncSession from a fresh engine. We
    TRUNCATE the domain tables on teardown so the *next* test starts
    from a clean state regardless of what this test committed.
  * The SAVEPOINT/join-transaction-mode pattern is appealing on paper
    but fragile with asyncpg; we keep the simpler real-commit + truncate
    pattern.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.core.database import Base

# Import all models so their metadata is registered on Base before
# create_all is called.
import app.models  # noqa: F401  (triggers __init__.py imports)


_TABLES_TO_TRUNCATE = (
    "transactions",
    "positions",
    "screen_history",
    "conviction_history",
    "fair_value_history",
    "financials_cache",  # Step 2 technical cache table
    "yfinance_cache",  # Step 5 technical cache table
)


@pytest.fixture()
async def engine():
    """Function-scoped async engine (see module docstring)."""
    _engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        future=True,
    )
    # Ensure all tables exist (idempotent — leaves existing rows untouched).
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield _engine

    await _engine.dispose()


@pytest.fixture()
async def db(engine) -> AsyncSession:
    """Function-scoped DB session, real commits, truncate on teardown."""
    session_factory = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with session_factory() as session:
        yield session

    async with engine.begin() as conn:
        await conn.execute(
            text(f"TRUNCATE {', '.join(_TABLES_TO_TRUNCATE)} RESTART IDENTITY CASCADE")
        )
