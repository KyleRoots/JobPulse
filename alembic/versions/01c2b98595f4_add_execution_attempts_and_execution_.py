"""Add execution_attempts and execution_history to support_ticket

Revision ID: 01c2b98595f4
Revises: 1812966d184b
Create Date: 2026-03-26 21:07:56.746303

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '01c2b98595f4'
down_revision: Union[str, Sequence[str], None] = '1812966d184b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('support_ticket', sa.Column('execution_attempts', sa.Integer(), nullable=False, server_default='0'))
    op.add_column('support_ticket', sa.Column('execution_history', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('support_ticket', 'execution_history')
    op.drop_column('support_ticket', 'execution_attempts')
