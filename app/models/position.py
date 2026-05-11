"""positions — master-prompt §11.2.

One row per holding. Quantities, average cost, weight, last price are NOT
stored: they derive from `transactions` (qty/price/fee) joined with live
market data fetched at read time.
"""

import uuid
from datetime import datetime

from sqlalchemy import CHAR, DateTime, Index, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Position(Base):
    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    symbol: Mapped[str] = mapped_column(Text, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (Index("ix_positions_symbol", "symbol"),)
