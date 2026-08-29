"""add hiring_signal_outreach message type

Revision ID: f1a2b3c4d5e6
Revises: e5f6a7b8c9d0
Create Date: 2026-08-27 00:00:00.000000

Adds "hiring_signal_outreach" to the message_type Postgres enum, for the
new Hiring-Signal Outreach Agent (POST /bd-chat/.../hiring-signal-outreach)
-- its assistant messages carry a fixed 5-part output (company
understanding, commercial interpretation, MOTM fit, WhatsApp sequence,
canned replies) that the frontend needs to render differently from a
"strategy" or "pitch" message, hence the new distinct type rather than
reusing one of the existing two.

Same transaction-safety note as e5f6a7b8c9d0: ALTER TYPE ... ADD VALUE is
safe inside a transaction on Postgres 12+ as long as the new value isn't
used in the same transaction it's added in, which this migration doesn't
do.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'f1a2b3c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'e5f6a7b8c9d0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("ALTER TYPE message_type ADD VALUE IF NOT EXISTS 'hiring_signal_outreach'")


def downgrade() -> None:
    """Downgrade schema.

    Postgres has no DROP VALUE for enums -- see e5f6a7b8c9d0's downgrade()
    docstring for the same reasoning, which applies identically here.
    """
    raise NotImplementedError(
        "Downgrading past f1a2b3c4d5e6 requires manually rebuilding the "
        "message_type enum type -- see docstring."
    )
