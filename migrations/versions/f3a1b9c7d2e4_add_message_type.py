"""add message_type to messages

Revision ID: f3a1b9c7d2e4
Revises: a1b2c3d4e5f6
Create Date: 2026-08-21 00:00:00.000000

Adds messages.message_type ('strategy'/'pitch'), distinguishing the two
kinds of assistant-authored turns the /strategy and /strategy/stream
endpoints can now produce. User-sender rows always stay NULL. Existing
assistant rows are backfilled to 'strategy' since every one predates the
pitch-generation feature.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f3a1b9c7d2e4'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    message_type_enum = postgresql.ENUM(
        "strategy", "pitch", name="message_type", create_type=False
    )
    message_type_enum.create(op.get_bind())

    op.add_column(
        'messages', sa.Column('message_type', message_type_enum, nullable=True)
    )

    op.execute(
        "UPDATE messages SET message_type = 'strategy' WHERE sender = 'assistant'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('messages', 'message_type')
    postgresql.ENUM(name="message_type").drop(op.get_bind())
