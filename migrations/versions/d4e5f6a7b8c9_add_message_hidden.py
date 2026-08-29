"""add hidden flag to messages

Revision ID: d4e5f6a7b8c9
Revises: b7c4d8e1a3f2
Create Date: 2026-08-25 00:00:00.000000

Adds messages.hidden, marking scaffolding rows the pipeline persists for
its own later use (e.g. the enriched-situation seed message a direct
pitch-intent turn writes before generating the real pitch) but that were
never meant to be rendered as a chat bubble. GET .../messages now excludes
hidden rows; the pipeline still reads them directly by DB query. Existing
rows default to false (visible), matching current behavior.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'd4e5f6a7b8c9'
down_revision: Union[str, Sequence[str], None] = 'b7c4d8e1a3f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'messages',
        sa.Column('hidden', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('messages', 'hidden')
