"""add candidate country correction audit log

Revision ID: f1a2b3c4d5e6
Revises: e0f1a2b3c4d5
Create Date: 2026-07-30
"""
from alembic import op
import sqlalchemy as sa


revision = "f1a2b3c4d5e6"
down_revision = "e0f1a2b3c4d5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_country_correction_log",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=True),
        sa.Column("bullhorn_candidate_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=255), nullable=True),
        sa.Column("previous_country_name", sa.String(length=255), nullable=True),
        sa.Column("previous_country_id", sa.Integer(), nullable=True),
        sa.Column("corrected_country_name", sa.String(length=255), nullable=False),
        sa.Column("corrected_country_id", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.String(length=20), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("trigger", sa.String(length=50), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["environment_id"],
            ["bullhorn_environment.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_candidate_country_correction_log_environment_id",
        "candidate_country_correction_log",
        ["environment_id"],
        unique=False,
    )
    op.create_index(
        "ix_candidate_country_correction_log_bullhorn_candidate_id",
        "candidate_country_correction_log",
        ["bullhorn_candidate_id"],
        unique=False,
    )
    op.create_index(
        "ix_candidate_country_correction_log_status",
        "candidate_country_correction_log",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_candidate_country_correction_log_created_at",
        "candidate_country_correction_log",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        "idx_country_correction_candidate_created",
        "candidate_country_correction_log",
        ["bullhorn_candidate_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_country_correction_env_candidate",
        "candidate_country_correction_log",
        ["environment_id", "bullhorn_candidate_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_country_correction_env_candidate",
        table_name="candidate_country_correction_log",
    )
    op.drop_index(
        "idx_country_correction_candidate_created",
        table_name="candidate_country_correction_log",
    )
    op.drop_index(
        "ix_candidate_country_correction_log_created_at",
        table_name="candidate_country_correction_log",
    )
    op.drop_index(
        "ix_candidate_country_correction_log_status",
        table_name="candidate_country_correction_log",
    )
    op.drop_index(
        "ix_candidate_country_correction_log_bullhorn_candidate_id",
        table_name="candidate_country_correction_log",
    )
    op.drop_index(
        "ix_candidate_country_correction_log_environment_id",
        table_name="candidate_country_correction_log",
    )
    op.drop_table("candidate_country_correction_log")
