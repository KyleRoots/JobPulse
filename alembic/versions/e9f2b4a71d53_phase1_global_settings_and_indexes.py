"""Phase 1 refactoring: GlobalSettings columns and missing indexes

Adds description and category columns to global_settings table.
Adds indexes on frequently-queried email and reference columns.

All changes are idempotent (IF NOT EXISTS / existence checks).

Revision ID: e9f2b4a71d53
Revises: d8a3f5e71c42
Create Date: 2026-02-24 02:15:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'e9f2b4a71d53'
down_revision: Union[str, Sequence[str], None] = 'd8a3f5e71c42'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_COLUMNS = [
    ('global_settings', 'description', sa.String(500), True),
    ('global_settings', 'category', sa.String(50), True),
]

_NEW_INDEXES = [
    ('idx_global_settings_category', 'global_settings', 'category'),
    ('idx_job_reference_number_ref', 'job_reference_number', 'reference_number'),
    ('idx_email_delivery_log_recipient', 'email_delivery_log', 'recipient_email'),
    ('idx_parsed_email_candidate_email', 'parsed_email', 'candidate_email'),
    ('idx_candidate_vetting_log_candidate_email', 'candidate_vetting_log', 'candidate_email'),
    ('idx_scout_vetting_session_candidate_email', 'scout_vetting_session', 'candidate_email'),
]


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)

    for table, col_name, col_type, nullable in _NEW_COLUMNS:
        existing_cols = [c['name'] for c in inspector.get_columns(table)]
        if col_name not in existing_cols:
            op.add_column(table, sa.Column(col_name, col_type, nullable=nullable))

    for idx_name, table, column in _NEW_INDEXES:
        existing = [idx['name'] for idx in inspector.get_indexes(table)]
        if idx_name not in existing:
            op.create_index(idx_name, table, [column])


def downgrade() -> None:
    for idx_name, table, _ in reversed(_NEW_INDEXES):
        op.drop_index(idx_name, table_name=table)

    for table, col_name, _, _ in reversed(_NEW_COLUMNS):
        op.drop_column(table, col_name)
