"""transactions — master-prompt §11.2.

Immutable ledger linked to a position. Quantity sign convention:
positive qty = buy, negative qty = sell. No tx_type column in the spec.
"""

import uuid
from datetime import date as date_type
from decimal import Decimal

from sqlalchemy import Date, ForeignKey, Index, Numeric
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    position_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("positions.id", ondelete="CASCADE"),
        nullable=False,
    )
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    qty: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(18, 8), nullable=False)
    fee: Mapped[Decimal] = mapped_column(
        Numeric(10, 4), nullable=False, default=Decimal("0")
    )

    __table_args__ = (
        Index("ix_transactions_position_id", "position_id"),
        Index("ix_transactions_date", "date"),
    )
