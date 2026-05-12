"""003 — purge corrupted screen_history rows polluted by the empty-ratios bug.

Revision ID: 003
Revises: 002
Create Date: 2026-05-12 00:00:00.000000

Context
-------
During the Step 1 → Step 1.5 deploy saga, ``app/services/shariah_service.py``
silently defaulted missing or null upstream ratios to ``0.0`` (per a naive
reading of spec §5.2's ``ratios.get(field, 0)``). Every Halal Terminal call
that returned an empty / partial payload produced a verdict ``PASS`` row in
``screen_history`` with an unusable ``raw_ratios`` object: a halal gate that
asserts PASS without any factual basis.

The service hardening landing in the same commit (Fix A) prevents new
corrupted rows from being inserted. This migration scrubs the rows that
were inserted before Fix A landed, so the cache stops serving toxic
verdicts during the 7-day TTL window.

Targeted pollution shapes (both inserted by the bug):
  1. ``ratios_json`` is ``NULL`` or ``'{}'`` (extremely empty)
  2. ``ratios_json->'raw_ratios'`` is ``NULL`` or ``'{}'`` (no inner dict)
  3. ``ratios_json->'raw_ratios'`` has the 7 ShariahRatios keys but the 4
     bloquant ratios are all NULL — the typical shape produced by the bug
     since pydantic's ShariahRatios fills defaults.

A legitimate PASS row would have at least one of the 4 bloquant ratios
present as a number, so this DELETE is conservative — it can't remove a
truly informed PASS.

Idempotent: re-running this migration deletes 0 rows once the table is
clean.

Downgrade: no-op. The deleted rows were toxic and cannot be reconstituted.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM screen_history
        WHERE verdict = 'PASS'
          AND (
            -- Shape A: ratios_json is empty or missing entirely.
            ratios_json IS NULL
            OR ratios_json::text = '{}'
            -- Shape B: raw_ratios key is missing or its value is empty/null.
            OR ratios_json->>'raw_ratios' IS NULL
            OR ratios_json->>'raw_ratios' = '{}'
            -- Shape C: raw_ratios object exists but all 4 bloquant ratios are
            -- absent or null — the most common pollution shape produced by the
            -- pre-Fix-A bug (pydantic ShariahRatios fills None for absent keys).
            OR (
              ratios_json->'raw_ratios'->>'debt_to_marketcap' IS NULL
              AND ratios_json->'raw_ratios'->>'cash_to_marketcap' IS NULL
              AND ratios_json->'raw_ratios'->>'impure_revenue_ratio' IS NULL
              AND ratios_json->'raw_ratios'->>'interest_income_ratio' IS NULL
            )
          );
        """
    )


def downgrade() -> None:
    # No-op: deleted rows cannot be reconstituted (and were toxic anyway).
    pass
