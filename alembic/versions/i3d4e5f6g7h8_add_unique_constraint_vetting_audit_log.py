"""Add unique constraint to VettingAuditLog.candidate_vetting_log_id

Prevents two overlapping Quality Auditor cycles from writing duplicate
audit log rows for the same candidate (race between manual trigger and
the 15-minute scheduled tick, or two long-running cycles overlapping).

Drops the old non-unique index `idx_audit_log_vetting_id` (the unique
constraint creates its own implicit index, so the old one becomes
redundant). De-duplicates any existing rows first — keeping the oldest
row per candidate_vetting_log_id — so the constraint can be added on
production data without errors.

Revision ID: i3d4e5f6g7h8
Revises: h2c3d4e5f6g7
Create Date: 2026-04-25 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'i3d4e5f6g7h8'
down_revision: Union[str, Sequence[str], None] = 'h2c3d4e5f6g7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'vetting_audit_log' not in existing_tables:
        return

    op.execute(
        """
        DELETE FROM vetting_audit_log
        WHERE id NOT IN (
            SELECT MIN(id)
            FROM vetting_audit_log
            GROUP BY candidate_vetting_log_id
        )
        """
    )

    existing_indexes = {idx['name'] for idx in inspector.get_indexes('vetting_audit_log')}
    if 'idx_audit_log_vetting_id' in existing_indexes:
        op.drop_index('idx_audit_log_vetting_id', table_name='vetting_audit_log')

    existing_uqs = {uq['name'] for uq in inspector.get_unique_constraints('vetting_audit_log')}
    if 'uq_audit_log_vetting_id' not in existing_uqs:
        op.create_unique_constraint(
            'uq_audit_log_vetting_id',
            'vetting_audit_log',
            ['candidate_vetting_log_id'],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'vetting_audit_log' not in existing_tables:
        return

    existing_uqs = {uq['name'] for uq in inspector.get_unique_constraints('vetting_audit_log')}
    if 'uq_audit_log_vetting_id' in existing_uqs:
        op.drop_constraint(
            'uq_audit_log_vetting_id',
            'vetting_audit_log',
            type_='unique',
        )

    existing_indexes = {idx['name'] for idx in inspector.get_indexes('vetting_audit_log')}
    if 'idx_audit_log_vetting_id' not in existing_indexes:
        op.create_index(
            'idx_audit_log_vetting_id',
            'vetting_audit_log',
            ['candidate_vetting_log_id'],
        )
