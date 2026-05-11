"""conviction_history — master-prompt §11.2.

Time-series of conviction snapshots produced by the SCORE engine
(refonte planifiée étape 4). `tier` is a coarse label; `score` is the
underlying continuous value.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ConvictionHistory(Base):
    __tablename__ = "conviction_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    score: Mapped[float] = mapped_column(Float, nullable=False)
    tier: Mapped[str] = mapped_column(Text, nullable=False)

    __table_args__ = (
        Index("ix_conviction_history_symbol", "symbol"),
        Index("ix_conviction_history_symbol_computed_at", "symbol", "computed_at"),
    )
