"""add placement_margin_calc_log table

Revision ID: t4n5o6p7q8r9
Revises: s3m4n5o6p7q8
Create Date: 2026-05-18

Audit trail for the Placement Net Margin % calculator. One row per
attempt — input snapshot, computed value, status, and PATCH outcome —
so Finance can retroactively trace any number on a Bullhorn placement
back to the inputs that produced it.
"""
from alembic import op
import sqlalchemy as sa


revision = 't4n5o6p7q8r9'
down_revision = 's3m4n5o6p7q8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'placement_margin_calc_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('bullhorn_placement_id', sa.Integer(), nullable=False),
        sa.Column('trigger', sa.String(length=20), nullable=False,
                  server_default='webhook'),
        sa.Column('client_bill_rate', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('pay_rate', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('custom_bill_rate_1', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('custom_bill_rate_2', sa.Numeric(precision=12, scale=4), nullable=True),
        sa.Column('computed_margin_pct', sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column('calc_status', sa.String(length=40), nullable=False),
        sa.Column('write_success', sa.Boolean(), nullable=True),
        sa.Column('write_skipped_reason', sa.String(length=80), nullable=True),
        sa.Column('bullhorn_error', sa.Text(), nullable=True),
        sa.Column('bullhorn_event_id', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        'ix_placement_margin_calc_log_created_at',
        'placement_margin_calc_log', ['created_at'],
    )
    op.create_index(
        'ix_placement_margin_calc_log_bullhorn_placement_id',
        'placement_margin_calc_log', ['bullhorn_placement_id'],
    )
    op.create_index(
        'ix_placement_margin_calc_log_placement_created',
        'placement_margin_calc_log', ['bullhorn_placement_id', 'created_at'],
    )
    op.create_index(
        'ix_placement_margin_calc_log_status_created',
        'placement_margin_calc_log', ['calc_status', 'created_at'],
    )


def downgrade() -> None:
    op.drop_index('ix_placement_margin_calc_log_status_created',
                  table_name='placement_margin_calc_log')
    op.drop_index('ix_placement_margin_calc_log_placement_created',
                  table_name='placement_margin_calc_log')
    op.drop_index('ix_placement_margin_calc_log_bullhorn_placement_id',
                  table_name='placement_margin_calc_log')
    op.drop_index('ix_placement_margin_calc_log_created_at',
                  table_name='placement_margin_calc_log')
    op.drop_table('placement_margin_calc_log')
