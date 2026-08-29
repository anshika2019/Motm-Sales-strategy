"""add conversation memory summary and feedback upsert index

Revision ID: a1b2c3d4e5f6
Revises: 894de2b8cebd
Create Date: 2026-08-21 00:00:00.000000

Adds conversations.memory_summary (rolling long-term memory summary carried
across a user's conversations) and a unique index on feedback
(message_id, user_id) so a user's thumbs rating on a message can be upserted
(changed) rather than accumulating duplicate rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '894de2b8cebd'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('conversations', sa.Column('memory_summary', sa.Text(), nullable=True))
    op.create_index(
        'feedback_message_user_unique', 'feedback', ['message_id', 'user_id'], unique=True
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('feedback_message_user_unique', table_name='feedback')
    op.drop_column('conversations', 'memory_summary')
