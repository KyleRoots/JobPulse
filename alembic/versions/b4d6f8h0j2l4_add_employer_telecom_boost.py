"""Add employer_telecom_boost to job_vetting_requirements

Revision ID: b4d6f8h0j2l4
Revises: a3c5e7g9i1k2
Create Date: 2026-08-26

Per-job +5 boost for listed telecom/satellite employers (Adam / Telesat).
"""
from alembic import op
import sqlalchemy as sa


revision = "b4d6f8h0j2l4"
down_revision = "a3c5e7g9i1k2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_vetting_requirements",
        sa.Column(
            "employer_telecom_boost",
            sa.Boolean(),
            nullable=True,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("job_vetting_requirements", "employer_telecom_boost")
