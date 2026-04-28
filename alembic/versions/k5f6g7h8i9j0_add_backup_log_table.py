"""Add backup_log table for nightly database backups

Adds the ``backup_log`` table that tracks every database backup run —
status, file name, size, OneDrive item ID, duration, and error messages.
Used by the admin backup dashboard and the nightly scheduler job.

Revision ID: k5f6g7h8i9j0
Revises: j4e5f6g7h8i9
Create Date: 2026-04-28 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'k5f6g7h8i9j0'
down_revision: Union[str, Sequence[str], None] = 'j4e5f6g7h8i9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'backup_log' not in existing_tables:
        op.create_table(
            'backup_log',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('started_at', sa.DateTime(), nullable=False),
            sa.Column('completed_at', sa.DateTime(), nullable=True),
            sa.Column('status', sa.String(length=20), nullable=False, server_default='running'),
            sa.Column('file_name', sa.String(length=500), nullable=True),
            sa.Column('file_size_bytes', sa.BigInteger(), nullable=True),
            sa.Column('onedrive_item_id', sa.String(length=255), nullable=True),
            sa.Column('onedrive_web_url', sa.String(length=1000), nullable=True),
            sa.Column('error_message', sa.Text(), nullable=True),
            sa.Column('duration_seconds', sa.Float(), nullable=True),
            sa.Column('triggered_by', sa.String(length=50), nullable=False, server_default='scheduler'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('idx_backup_log_started_at', 'backup_log', ['started_at'])
        op.create_index('idx_backup_log_status', 'backup_log', ['status'])


def downgrade() -> None:
    op.drop_index('idx_backup_log_status', table_name='backup_log')
    op.drop_index('idx_backup_log_started_at', table_name='backup_log')
    op.drop_table('backup_log')
