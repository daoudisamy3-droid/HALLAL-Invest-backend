"""
Root conftest.py — pytest fixtures for the Hallal Invest backend test suite.

Spec §11.2 §0.3: async engine + SAVEPOINT rollback fixture so tests never
persist real data to the database.
"""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import Base

# Import all models so their metadata is registered on Base before
# create_all / reflect is called.
import app.models  # noqa: F401  (triggers __init__.py imports)


@pytest.fixture(scope="session")
async def engine():
    """Session-scoped async engine pointing at the test database."""
    _engine = create_async_engine(
        settings.DATABASE_URL,
        echo=False,
        future=True,
    )
    # Ensure all tables exist (idempotent — does not drop existing data).
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    yield _engine

    await _engine.dispose()


@pytest.fixture()
async def db(engine) -> AsyncSession:
    """
    Function-scoped DB session wrapped in a SAVEPOINT transaction.

    Each test gets a clean, isolated view of the database:
    - A real transaction is opened on the connection.
    - A SAVEPOINT is created inside it.
    - The session uses that connection directly (no pool checkout).
    - After the test the SAVEPOINT is rolled back, then the outer
      transaction is rolled back — nothing is committed to disk.
    """
    async with engine.connect() as conn:
        await conn.begin()  # outer transaction (never committed)
        await conn.begin_nested()  # SAVEPOINT

        session_factory = async_sessionmaker(
            bind=conn,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        async with session_factory() as session:
            yield session
            await session.rollback()

        # Roll back the outer transaction so nothing leaks.
        await conn.rollback()
