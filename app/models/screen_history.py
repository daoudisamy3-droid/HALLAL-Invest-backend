"""screen_history — master-prompt §11.2.

Audit trail of Shariah screening runs. Ratios are stored as JSONB so the
shape can evolve (add/remove ratios) without an Alembic migration.
"""

import uuid
from datetime import date as date_type
from typing import Any

from sqlalchemy import Date, Index, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ScreenHistory(Base):
    __tablename__ = "screen_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    screen_date: Mapped[date_type] = mapped_column(Date, nullable=False)
    ratios_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_screen_history_symbol", "symbol"),
        Index("ix_screen_history_symbol_screen_date", "symbol", "screen_date"),
    )
