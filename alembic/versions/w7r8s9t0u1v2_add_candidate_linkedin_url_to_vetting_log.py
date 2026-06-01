"""add candidate_linkedin_url to candidate_vetting_log

Revision ID: w7r8s9t0u1v2
Revises: v6q7r8s9t0u1
Create Date: 2026-06-01

Adds a canonical LinkedIn URL column (plus its index) to candidate_vetting_log
so the fraud LinkedIn-reuse signal can spot one profile URL claimed across many
candidate identities via a plain indexed equality lookup. The URL is captured
universally from resume text. Boot-time idempotent ALTER in seeding/migrations.py
covers dev DBs; this keeps production schema in parity.
"""
from alembic import op
import sqlalchemy as sa


revision = "w7r8s9t0u1v2"
down_revision = "v6q7r8s9t0u1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_vetting_log",
        sa.Column("candidate_linkedin_url", sa.String(length=255), nullable=True),
    )
    op.create_index(
        "ix_candidate_vetting_log_candidate_linkedin_url",
        "candidate_vetting_log", ["candidate_linkedin_url"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_vetting_log_candidate_linkedin_url",
        table_name="candidate_vetting_log",
    )
    op.drop_column("candidate_vetting_log", "candidate_linkedin_url")
