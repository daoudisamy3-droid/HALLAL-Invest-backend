"""006 — yfinance_cache table (Step 5 YFinance caching, technical only).

Revision ID: 006
Revises: 005
Create Date: 2026-05-12 00:00:00.000000

Scope note
----------
Like ``financials_cache`` (alembic 005), this table is NOT in spec §11.2.
It is a technical caching layer for YFinance payloads (Step 5):

  - ``payload_kind = 'info'``     — Yahoo .info dict, TTL 1h.
  - ``payload_kind = 'history'``  — monthly OHLC bars, TTL 24h.

Two distinct TTLs justify a discriminator column rather than two tables
(YAGNI: one new model is enough).

Schema
------
- ``id``           UUID PK
- ``ticker``       TEXT NOT NULL — normalized uppercase
- ``payload_kind`` TEXT NOT NULL — 'info' | 'history'
- ``params``       TEXT NOT NULL — extra discriminator (e.g. 'period=5y/interval=1mo')
- ``fetched_at``   TIMESTAMPTZ NOT NULL
- ``payload_json`` JSONB NOT NULL — raw provider response

Indexes:
- ``ix_yfinance_cache_lookup`` on (ticker, payload_kind, params, fetched_at)
  — supports the cache-hit pattern
    ``WHERE ticker=? AND payload_kind=? AND params=? AND fetched_at>=? ORDER BY fetched_at DESC LIMIT 1``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "yfinance_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("payload_kind", sa.Text(), nullable=False),
        sa.Column("params", sa.Text(), nullable=False, server_default=""),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("payload_json", postgresql.JSONB(), nullable=False),
    )
    op.create_index(
        "ix_yfinance_cache_lookup",
        "yfinance_cache",
        ["ticker", "payload_kind", "params", "fetched_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_yfinance_cache_lookup", table_name="yfinance_cache")
    op.drop_table("yfinance_cache")
