"""005 — financials_cache table (Step 2 SEC EDGAR caching, technical only).

Revision ID: 005
Revises: 004
Create Date: 2026-05-12 00:00:00.000000

Scope note
----------
This table is NOT in spec §11.2 (which listed 5 domain tables: positions,
transactions, screen_history, conviction_history, fair_value_history).
It is a purely **technical** caching layer for the SEC EDGAR
``company_facts`` payload (spec §3.2.2 — TTL 24h). The domain model is
unchanged.

When the caching layer eventually migrates to Redis (cf. step 7
raffinements roadmap), this table can be dropped via a `down_revision`
chain or a dedicated cleanup migration.

Schema
------
- ``id``        UUID PK
- ``ticker``    TEXT NOT NULL — normalized uppercase
- ``cik``       BIGINT NOT NULL — SEC Central Index Key
- ``fetched_at`` TIMESTAMPTZ NOT NULL — when the SEC payload was fetched
- ``facts_json`` JSONB NOT NULL — full ``company_facts`` blob from SEC

Indexes:
- ``ix_financials_cache_ticker`` on (ticker)
- ``ix_financials_cache_ticker_fetched_at_desc`` on (ticker, fetched_at DESC)
  — supports the cache-hit lookup pattern:
    ``WHERE ticker = ? AND fetched_at >= now() - 24h ORDER BY fetched_at DESC LIMIT 1``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "financials_cache",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ticker", sa.Text(), nullable=False),
        sa.Column("cik", sa.BigInteger(), nullable=False),
        sa.Column(
            "fetched_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("facts_json", postgresql.JSONB(), nullable=False),
    )
    op.create_index("ix_financials_cache_ticker", "financials_cache", ["ticker"])
    op.create_index(
        "ix_financials_cache_ticker_fetched_at_desc",
        "financials_cache",
        ["ticker", sa.text("fetched_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_financials_cache_ticker_fetched_at_desc", table_name="financials_cache")
    op.drop_index("ix_financials_cache_ticker", table_name="financials_cache")
    op.drop_table("financials_cache")
