"""add approval flag to profiles

Revision ID: a3b4c5d6e7f8
Revises: f1a2b3c4d5e6
Create Date: 2026-09-01 00:00:00.000000

Adds profiles.is_approved, gating self-service signups behind admin
approval (see POST /auth/signup and POST /admin/users/{id}/approve in
app/routers/auth.py / app/routers/admin.py). Column defaults to false so
every new row (including ones inserted by the on_auth_user_created trigger,
which doesn't set this column explicitly) starts unapproved -- but existing
rows are immediately backfilled to true so no account already in use today
gets locked out.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3b4c5d6e7f8'
down_revision: Union[str, Sequence[str], None] = 'f1a2b3c4d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'profiles',
        sa.Column('is_approved', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        schema='public',
    )
    op.execute("update public.profiles set is_approved = true")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('profiles', 'is_approved', schema='public')
