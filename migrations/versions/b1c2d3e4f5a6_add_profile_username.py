"""add username to profiles

Revision ID: b1c2d3e4f5a6
Revises: a3b4c5d6e7f8
Create Date: 2026-09-02 00:00:00.000000

Adds profiles.username, a self-set display handle editable from the new
Settings page (PATCH /auth/me in app/routers/auth.py). Nullable -- every
existing row starts with none -- and uniquely indexed only where set (a
partial index, so multiple NULLs are still allowed, unlike a plain unique
constraint).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a6'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('profiles', sa.Column('username', sa.Text(), nullable=True), schema='public')
    op.create_index(
        'profiles_username_unique',
        'profiles',
        ['username'],
        unique=True,
        schema='public',
        postgresql_where=sa.text('username IS NOT NULL'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('profiles_username_unique', table_name='profiles', schema='public')
    op.drop_column('profiles', 'username', schema='public')
