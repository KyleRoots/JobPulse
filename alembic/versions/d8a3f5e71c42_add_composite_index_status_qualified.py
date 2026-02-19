"""Add composite index on candidate_vetting_log(status, is_qualified)

Performance optimization for the /screening dashboard stats query.
The aggregation query at routes/vetting.py:66 does COUNT/SUM with CASE
on status and is_qualified — this composite index covers both columns.

Revision ID: d8a3f5e71c42
Revises: c7e2a4f3b9d1
Create Date: 2026-02-19 12:45:00.000000

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'd8a3f5e71c42'
down_revision: Union[str, None] = 'c7e2a4f3b9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        'ix_candidate_vetting_log_status_qualified',
        'candidate_vetting_log',
        ['status', 'is_qualified'],
        unique=False
    )


def downgrade() -> None:
    op.drop_index(
        'ix_candidate_vetting_log_status_qualified',
        table_name='candidate_vetting_log'
    )
