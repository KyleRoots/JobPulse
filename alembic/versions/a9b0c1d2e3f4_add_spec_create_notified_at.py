"""add spec_create_notified_at to job_vetting_requirements

Revision ID: a9b0c1d2e3f4
Revises: f1a2b3c4d5e6
Create Date: 2026-08-05

Tracks when the create-only requirements-spec sanity email was sent.
NULL means deferred until the job appears on the Scout Screening
snapshot list (BullhornMonitor.last_job_snapshot). Existing rows are
backfilled so historical specs are not re-emailed after deploy.
"""
from alembic import op
import sqlalchemy as sa


revision = "a9b0c1d2e3f4"
down_revision = "f1a2b3c4d5e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "job_vetting_requirements",
        sa.Column("spec_create_notified_at", sa.DateTime(), nullable=True),
    )
    # Treat pre-existing specs as already handled (including ones that already
    # emailed under the pre-gate notify path).
    op.execute(
        """
        UPDATE job_vetting_requirements
        SET spec_create_notified_at = COALESCE(
            created_at,
            last_ai_interpretation,
            CURRENT_TIMESTAMP
        )
        WHERE ai_interpreted_requirements IS NOT NULL
          AND spec_create_notified_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("job_vetting_requirements", "spec_create_notified_at")
