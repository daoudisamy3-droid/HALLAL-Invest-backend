"""yfinance_cache — Yahoo Finance payload caching layer.

Step 5 technical table (NOT in spec §11.2). See alembic 006 and
``app/services/yfinance_service.py`` for the cache orchestration.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class YFinanceCache(Base):
    __tablename__ = "yfinance_cache"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(Text, nullable=False)
    payload_kind: Mapped[str] = mapped_column(Text, nullable=False)
    params: Mapped[str] = mapped_column(Text, nullable=False, default="")
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    payload_json: Mapped[dict[str, Any] | list[Any]] = mapped_column(
        JSONB, nullable=False
    )

    __table_args__ = (
        Index(
            "ix_yfinance_cache_lookup",
            "ticker",
            "payload_kind",
            "params",
            "fetched_at",
        ),
    )
