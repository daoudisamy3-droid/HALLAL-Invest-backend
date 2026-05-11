"""001 initial schema — 5 tables per master-prompt §11.2

Revision ID: 001
Revises:
Create Date: 2026-05-11 00:00:00.000000

Schema authority: master-prompt.md §11.2.
Strict minimal columns: derived data (quantity, weight, halal_score…) is
computed on the fly from `transactions` + external APIs, not stored.
Flexible payloads (`ratios_json`, `methods_json`) live as JSONB to avoid
migration churn as scoring evolves.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "positions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("currency", sa.CHAR(3), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_positions_symbol", "positions", ["symbol"])

    op.create_table(
        "transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "position_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("positions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("qty", sa.Numeric(18, 8), nullable=False),
        sa.Column("price", sa.Numeric(18, 8), nullable=False),
        sa.Column("fee", sa.Numeric(10, 4), nullable=False, server_default="0"),
    )
    op.create_index("ix_transactions_position_id", "transactions", ["position_id"])
    op.create_index("ix_transactions_date", "transactions", ["date"])

    op.create_table(
        "screen_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("screen_date", sa.Date(), nullable=False),
        sa.Column("ratios_json", postgresql.JSONB(), nullable=False),
        sa.Column("verdict", sa.Text(), nullable=False),
    )
    op.create_index("ix_screen_history_symbol", "screen_history", ["symbol"])
    op.create_index(
        "ix_screen_history_symbol_screen_date",
        "screen_history",
        ["symbol", "screen_date"],
    )

    op.create_table(
        "conviction_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
    )
    op.create_index("ix_conviction_history_symbol", "conviction_history", ["symbol"])
    op.create_index(
        "ix_conviction_history_symbol_computed_at",
        "conviction_history",
        ["symbol", "computed_at"],
    )

    op.create_table(
        "fair_value_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("symbol", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fair_value", sa.Numeric(18, 4), nullable=False),
        sa.Column("methods_json", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_fair_value_history_symbol", "fair_value_history", ["symbol"])
    op.create_index(
        "ix_fair_value_history_symbol_computed_at",
        "fair_value_history",
        ["symbol", "computed_at"],
    )


def downgrade() -> None:
    op.drop_table("fair_value_history")
    op.drop_table("conviction_history")
    op.drop_table("screen_history")
    op.drop_table("transactions")
    op.drop_table("positions")
