"""zip apply ingest outcome columns

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-10-12

Parity record. Live databases pick these columns up from seeding/migrations.py.
"""
from alembic import op
import sqlalchemy as sa


revision = "e2f3a4b5c6d7"
down_revision = "d1e2f3a4b5c6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("zip_apply_delivery", sa.Column("ingest_status", sa.String(length=32), nullable=True))
    op.add_column("zip_apply_delivery", sa.Column("bullhorn_candidate_id", sa.Integer(), nullable=True))
    op.add_column("zip_apply_delivery", sa.Column("bullhorn_submission_id", sa.Integer(), nullable=True))
    op.add_column("zip_apply_delivery", sa.Column("ingest_error", sa.String(length=500), nullable=True))
    op.create_index("ix_zip_apply_delivery_ingest_status", "zip_apply_delivery", ["ingest_status"])


def downgrade() -> None:
    op.drop_index("ix_zip_apply_delivery_ingest_status", table_name="zip_apply_delivery")
    op.drop_column("zip_apply_delivery", "ingest_error")
    op.drop_column("zip_apply_delivery", "bullhorn_submission_id")
    op.drop_column("zip_apply_delivery", "bullhorn_candidate_id")
    op.drop_column("zip_apply_delivery", "ingest_status")
