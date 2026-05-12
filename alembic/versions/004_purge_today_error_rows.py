"""004 — purge today's pre-refactor cache rows to avoid stale ``checks`` shapes.

Revision ID: 004
Revises: 003
Create Date: 2026-05-12 00:00:00.000000

Context
-------
The Step 1.5 ``feat(shariah): adapt to Halal Terminal free tier`` refactor
changed both the verdict-computation logic AND the serialised ``ratios_json``
shape (free-tier mode emits empty ``checks`` and ``raw_ratios``).

Any ``screen_history`` row inserted *today* under the previous logic is
likely either an ERROR (which should not have been persisted but might
have leaked) or a stale-shape row that the new deserialiser still
handles gracefully but that would confuse a side-by-side comparison
during the post-deploy retest.

Safety net: delete same-day rows so the retest hits a clean slate. We
only touch ``screen_date >= CURRENT_DATE - 1`` to avoid wiping older,
legitimately-cached data.

Idempotent (re-running deletes 0 rows once the day is clean) and
downgrade is a no-op.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM screen_history
        WHERE verdict = 'ERROR'
           OR screen_date >= CURRENT_DATE - INTERVAL '1 day';
        """
    )


def downgrade() -> None:
    # No-op: deleted rows cannot be reconstituted (and they were transient anyway).
    pass
