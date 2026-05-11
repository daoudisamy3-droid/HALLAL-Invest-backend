"""002 — composite DESC index on screen_history(symbol, screen_date).

Speeds up the cache lookup pattern used by shariah_service:

    SELECT ... FROM screen_history
    WHERE symbol = :symbol AND screen_date >= :cutoff
    ORDER BY screen_date DESC LIMIT 1;

The 001 index (symbol, screen_date) ASC can serve this query via a
reverse scan, but a dedicated DESC variant removes the planner's
freedom and makes the cache-hit path index-only-scan friendly.
"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op


revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "ix_screen_history_symbol_screen_date_desc",
        "screen_history",
        ["symbol", sa.text("screen_date DESC")],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_screen_history_symbol_screen_date_desc",
        table_name="screen_history",
    )
