"""add candidate_phone to candidate_vetting_log

Revision ID: v6q7r8s9t0u1
Revises: u5p6q7r8s9t0
Create Date: 2026-05-30

Adds a digits-only normalized phone column (plus its index) to
candidate_vetting_log so the fraud identity-reuse-by-phone signal can spot one
number shared across many candidate names via a plain indexed equality lookup.
Boot-time idempotent ALTER in seeding/migrations.py covers dev DBs; this keeps
production schema in parity.
"""
from alembic import op
import sqlalchemy as sa


revision = "v6q7r8s9t0u1"
down_revision = "u5p6q7r8s9t0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_vetting_log",
        sa.Column("candidate_phone", sa.String(length=32), nullable=True),
    )
    op.create_index(
        "ix_candidate_vetting_log_candidate_phone",
        "candidate_vetting_log", ["candidate_phone"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_vetting_log_candidate_phone",
        table_name="candidate_vetting_log",
    )
    op.drop_column("candidate_vetting_log", "candidate_phone")
