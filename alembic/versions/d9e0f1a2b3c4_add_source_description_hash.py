"""add source_description_hash to job_vetting_requirements

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-07-29

Stores a SHA-256 of the Bullhorn job description that produced
ai_interpreted_requirements. check_and_refresh_changed_jobs uses it to skip
gpt-5.4 re-extraction when dateLastModified advances without the JD text
changing (recruiter reassignment, status flips, etc.).
"""
from alembic import op
import sqlalchemy as sa


revision = "d9e0f1a2b3c4"
down_revision = "c8d9e0f1a2b3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_vetting_requirements",
        sa.Column("source_description_hash", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_vetting_requirements", "source_description_hash")
