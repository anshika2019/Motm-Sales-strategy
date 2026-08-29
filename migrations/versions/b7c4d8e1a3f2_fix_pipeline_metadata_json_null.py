"""fix pipeline_metadata rows stored as JSON null instead of SQL NULL

Revision ID: b7c4d8e1a3f2
Revises: f3a1b9c7d2e4
Create Date: 2026-08-21 00:00:00.000000

SQLAlchemy's JSONB type defaulted to none_as_null=False, so every
pipeline_metadata=None assignment (the pitch-flow messages in
app/routers/chat.py) was persisted as the JSON scalar `null` rather than
SQL NULL. That silently broke every `.isnot(None)` filter used to find
"the last message with real pipeline_metadata" (_load_prior_pipeline_context),
since a JSON `null` value still passes `IS NOT NULL`. The column now
declares none_as_null=True (see app/db/models.py) so this can't recur; this
migration corrects rows already corrupted by the old behavior.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'b7c4d8e1a3f2'
down_revision: Union[str, Sequence[str], None] = 'f3a1b9c7d2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("UPDATE messages SET pipeline_metadata = NULL WHERE pipeline_metadata = 'null'::jsonb")


def downgrade() -> None:
    """Downgrade schema."""
    # No-op: converting corrected SQL NULL rows back to JSON `null` would be
    # reintroducing the bug on purpose, not a meaningful schema rollback.
    pass
