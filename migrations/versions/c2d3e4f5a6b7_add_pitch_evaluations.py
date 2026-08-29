"""add pitch evaluations

Revision ID: c2d3e4f5a6b7
Revises: b096cbd0b3e5
Create Date: 2026-08-25 15:10:00.000000

Adds pitch_evaluations, one row per generated pitch Message holding the
LLM-as-judge audit of that pitch against the W2R RAG Addendum rubric (see
app/services/prompts/pitch_evaluation_prompt.py and evaluate_pitch() in
app/services/llm.py), written by a background task after the pitch is
persisted and shown to the user.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a6b7'
down_revision: Union[str, Sequence[str], None] = 'b096cbd0b3e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('pitch_evaluations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('message_id', sa.UUID(), nullable=False),
    sa.Column('conversation_id', sa.UUID(), nullable=False),
    sa.Column('output_format', sa.Text(), nullable=False),
    sa.Column('rubric_results', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('overall_score', sa.Integer(), nullable=False),
    sa.Column('top_gaps', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['message_id'], ['messages.id'], name='pitch_evaluations_message_id_fkey', ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], name='pitch_evaluations_conversation_id_fkey', ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('pitch_evaluations_message_id_idx', 'pitch_evaluations', ['message_id'], unique=False)
    op.create_index('pitch_evaluations_conversation_id_idx', 'pitch_evaluations', ['conversation_id'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('pitch_evaluations_conversation_id_idx', table_name='pitch_evaluations')
    op.drop_index('pitch_evaluations_message_id_idx', table_name='pitch_evaluations')
    op.drop_table('pitch_evaluations')
