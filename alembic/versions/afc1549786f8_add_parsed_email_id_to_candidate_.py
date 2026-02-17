"""Add parsed_email_id to candidate_vetting_log

Revision ID: afc1549786f8
Revises: 
Create Date: 2026-02-17 13:47:44.837277

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'afc1549786f8'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add parsed_email_id column if it doesn't already exist (idempotent)."""
    conn = op.get_bind()
    inspector = inspect(conn)
    columns = [col['name'] for col in inspector.get_columns('candidate_vetting_log')]

    if 'parsed_email_id' not in columns:
        op.add_column('candidate_vetting_log',
            sa.Column('parsed_email_id', sa.Integer(), nullable=True))
        op.create_index('ix_candidate_vetting_log_parsed_email_id',
            'candidate_vetting_log', ['parsed_email_id'])


def downgrade() -> None:
    """Remove parsed_email_id column and its index."""
    op.drop_index('ix_candidate_vetting_log_parsed_email_id',
                  table_name='candidate_vetting_log')
    op.drop_column('candidate_vetting_log', 'parsed_email_id')

