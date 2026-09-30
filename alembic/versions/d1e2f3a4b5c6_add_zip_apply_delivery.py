"""add zip_apply_delivery table

Revision ID: d1e2f3a4b5c6
Revises: c9d1e3f5a7b9
Create Date: 2026-09-30

Records ZipRecruiter Apply webhook deliveries for later review.
"""
from alembic import op
import sqlalchemy as sa


revision = "d1e2f3a4b5c6"
down_revision = "c9d1e3f5a7b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "zip_apply_delivery",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("job_id", sa.String(length=120), nullable=True),
        sa.Column("response_id", sa.String(length=120), nullable=True),
        sa.Column("body_bytes", sa.Integer(), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("has_name", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_email", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_phone", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_resume", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("has_profile", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("signature_header_present", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("duplicate_of_id", sa.Integer(), nullable=True),
        sa.Column("field_names", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_zip_apply_delivery_created_at", "zip_apply_delivery", ["created_at"])
    op.create_index("ix_zip_apply_delivery_job_id", "zip_apply_delivery", ["job_id"])
    op.create_index("ix_zip_apply_delivery_response_id", "zip_apply_delivery", ["response_id"])


def downgrade() -> None:
    op.drop_index("ix_zip_apply_delivery_response_id", table_name="zip_apply_delivery")
    op.drop_index("ix_zip_apply_delivery_job_id", table_name="zip_apply_delivery")
    op.drop_index("ix_zip_apply_delivery_created_at", table_name="zip_apply_delivery")
    op.drop_table("zip_apply_delivery")
