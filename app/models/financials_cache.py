"""financials_cache — SEC EDGAR company-facts caching layer.

Step 2 technical table (NOT in spec §11.2). See alembic 005 for full
context and `app/services/financials_service.py` for the cache logic.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FinancialsCache(Base):
    __tablename__ = "financials_cache"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    cik: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    facts_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_financials_cache_ticker", "ticker"),
        # ASC composite; the DESC variant lives only in alembic 005 (Postgres
        # can scan ASC backwards for our ORDER BY fetched_at DESC LIMIT 1).
        Index("ix_financials_cache_ticker_fetched_at", "ticker", "fetched_at"),
    )
