"""fair_value_history — master-prompt §11.2.

Each row = one fair-value computation. The `methods_json` payload lists
which valuation methods (DCF, comparables, AI-blend…) contributed and
their per-method outputs, kept as JSONB for evolution.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, Index, Numeric, Text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class FairValueHistory(Base):
    __tablename__ = "fair_value_history"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fair_value: Mapped[Decimal] = mapped_column(Numeric(18, 4), nullable=False)
    methods_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        Index("ix_fair_value_history_symbol", "symbol"),
        Index("ix_fair_value_history_symbol_computed_at", "symbol", "computed_at"),
    )
