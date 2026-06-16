"""add per-environment Sales Rep sync config to bullhorn_environment

Revision ID: a1b2c3d4e5f6
Revises: z0a1b2c3d4e5
Create Date: 2026-06-16

Per-environment Sales Rep display-name sync configuration. Adds three nullable
columns to the bullhorn_environment registry so the sync can be replicated for
new tenants whose Bullhorn custom fields differ from Myticas's:

  salesrep_sync_enabled  — BOOLEAN, tri-state. NULL enables the sync ONLY for
                           the default (Myticas) environment; new tenants stay
                           OFF until explicitly enabled. True/False override.
  salesrep_source_field  — VARCHAR(50), custom field holding the numeric
                           CorporateUser ID. NULL resolves to 'customText3'.
  salesrep_display_field — VARCHAR(50), custom field that receives the resolved
                           sales-rep name (shown in the company header). NULL
                           resolves to 'customText6'.

All three are nullable with no backfill — the default environment leaves them
NULL and behaves byte-for-byte as before. Boot-time idempotent ALTERs in
seeding/migrations.py cover dev DBs; this keeps production schema in parity.
"""
from alembic import op
import sqlalchemy as sa


revision = "a1b2c3d4e5f6"
down_revision = "z0a1b2c3d4e5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bullhorn_environment",
        sa.Column("salesrep_sync_enabled", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "bullhorn_environment",
        sa.Column("salesrep_source_field", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "bullhorn_environment",
        sa.Column("salesrep_display_field", sa.String(length=50), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bullhorn_environment", "salesrep_display_field")
    op.drop_column("bullhorn_environment", "salesrep_source_field")
    op.drop_column("bullhorn_environment", "salesrep_sync_enabled")
