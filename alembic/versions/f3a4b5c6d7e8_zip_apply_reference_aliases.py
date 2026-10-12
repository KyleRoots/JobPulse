"""zip apply reference aliases

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-10-12

Parity record. Live databases create this table from the model at boot.
"""
from alembic import op
import sqlalchemy as sa


revision = "f3a4b5c6d7e8"
down_revision = "e2f3a4b5c6d7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "job_reference_alias",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("reference_number", sa.String(length=50), nullable=False),
        sa.Column("bullhorn_job_id", sa.String(length=50), nullable=False),
        sa.Column("retired_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reference_number"),
    )
    op.create_index("ix_job_reference_alias_reference_number", "job_reference_alias", ["reference_number"], unique=True)
    op.create_index("ix_job_reference_alias_bullhorn_job_id", "job_reference_alias", ["bullhorn_job_id"])


def downgrade() -> None:
    op.drop_index("ix_job_reference_alias_bullhorn_job_id", table_name="job_reference_alias")
    op.drop_index("ix_job_reference_alias_reference_number", table_name="job_reference_alias")
    op.drop_table("job_reference_alias")
