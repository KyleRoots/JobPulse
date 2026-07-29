"""add tearsheet_absent_since to job_vetting_requirements

Revision ID: e0f1a2b3c4d5
Revises: d9e0f1a2b3c4
Create Date: 2026-07-29

Records the first time a job was observed missing from the active tearsheet
set. Requirements cleanup debounces on this instead of deleting on the first
miss, which stops the delete/re-extract churn between
incremental_monitoring_service._log_auto_removal_activity and the
requirements-maintenance new-job extraction path.
"""
from alembic import op
import sqlalchemy as sa

revision = "e0f1a2b3c4d5"
down_revision = "d9e0f1a2b3c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_vetting_requirements",
        sa.Column("tearsheet_absent_since", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("job_vetting_requirements", "tearsheet_absent_since")
