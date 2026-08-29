"""add motm_bd persona

Revision ID: e5f6a7b8c9d0
Revises: c2d3e4f5a6b7
Create Date: 2026-08-26 00:00:00.000000

Adds the new "motm_bd" value to the persona and knowledge_persona Postgres
enums, for the new BD (Business Development -- selling MOTM itself) mode.
The existing "sell_motm" value stays exactly as-is and keeps meaning what it
already means for the shipped SE (Sales Engineer) pipeline; motm_bd is a
brand-new, distinct value rather than a rename/reuse of sell_motm or
support_customer -- see the BD build plan for the reasoning.

ALTER TYPE ... ADD VALUE is transaction-safe on Postgres 12+ (the version
Supabase runs), as long as the new value isn't used in the same transaction
it's added in -- which this migration doesn't do, so no special handling is
needed beyond the plain op.execute() calls below.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'e5f6a7b8c9d0'
down_revision: Union[str, Sequence[str], None] = 'c2d3e4f5a6b7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE persona ADD VALUE IF NOT EXISTS 'motm_bd'")
    op.execute("ALTER TYPE knowledge_persona ADD VALUE IF NOT EXISTS 'motm_bd'")


def downgrade() -> None:
    """Downgrade schema.

    Postgres has no DROP VALUE for enums -- removing a value requires
    rebuilding the type (rename old, create new without the value, cast
    every dependent column, drop old). Deliberately not implemented here:
    doing that safely requires knowing no motm_bd rows exist yet, which this
    migration can't guarantee at downgrade time. If a downgrade is ever
    actually needed, do it by hand after confirming no motm_bd data exists.
    """
    raise NotImplementedError(
        "Downgrading past e5f6a7b8c9d0 requires manually rebuilding the "
        "persona/knowledge_persona enum types -- see docstring."
    )
