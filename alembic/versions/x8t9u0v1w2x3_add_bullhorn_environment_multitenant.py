"""add bullhorn_environment registry + environment_id discriminator

Revision ID: x8t9u0v1w2x3
Revises: w7r8s9t0u1v2
Create Date: 2026-06-12

Multi-tenant Bullhorn backbone (Task #100). Creates the bullhorn_environment
registry table (one row per connected Bullhorn instance / tenant) and adds a
nullable environment_id discriminator (FK + index) to the ATS-scoped tables.

The column is nullable and backfilled to the default (Myticas) environment at
boot in seed_bullhorn_environment, so single-tenant behavior is unchanged.
Boot-time idempotent ALTERs in seeding/migrations.py cover dev DBs; this keeps
production schema in parity. Existing uniqueness constraints are intentionally
NOT modified here — that belongs with the write-path environment population in a
later increment.
"""
from alembic import op
import sqlalchemy as sa


revision = "x8t9u0v1w2x3"
down_revision = "w7r8s9t0u1v2"
branch_labels = None
depends_on = None


_ENV_TABLES = (
    "candidate_vetting_log",
    "candidate_job_match",
    "job_vetting_requirements",
    "parsed_email",
    "bullhorn_monitor",
    "candidate_fraud_assessment",
    "job_embedding",
    "candidate_profile_embedding",
    "recruiter_notification_ledger",
)


def upgrade() -> None:
    op.create_table(
        "bullhorn_environment",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("company_name", sa.String(length=120), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("bullhorn_client_id", sa.String(length=255), nullable=True),
        sa.Column("bullhorn_client_secret", sa.String(length=255), nullable=True),
        sa.Column("bullhorn_username", sa.String(length=255), nullable=True),
        sa.Column("bullhorn_password", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bullhorn_environment_key", "bullhorn_environment", ["key"], unique=True)
    op.create_index("ix_bullhorn_environment_is_default", "bullhorn_environment", ["is_default"])
    op.create_index("ix_bullhorn_environment_is_active", "bullhorn_environment", ["is_active"])
    # At most one default environment (partial unique index).
    op.create_index(
        "uq_bullhorn_environment_single_default",
        "bullhorn_environment", ["is_default"], unique=True,
        postgresql_where=sa.text("is_default"),
    )

    for table in _ENV_TABLES:
        op.add_column(table, sa.Column("environment_id", sa.Integer(), nullable=True))
        op.create_index(f"ix_{table}_environment_id", table, ["environment_id"])
        op.create_foreign_key(
            f"fk_{table}_environment_id",
            table, "bullhorn_environment",
            ["environment_id"], ["id"],
        )


def downgrade() -> None:
    for table in _ENV_TABLES:
        op.drop_constraint(f"fk_{table}_environment_id", table, type_="foreignkey")
        op.drop_index(f"ix_{table}_environment_id", table_name=table)
        op.drop_column(table, "environment_id")

    op.drop_index("uq_bullhorn_environment_single_default", table_name="bullhorn_environment")
    op.drop_index("ix_bullhorn_environment_is_active", table_name="bullhorn_environment")
    op.drop_index("ix_bullhorn_environment_is_default", table_name="bullhorn_environment")
    op.drop_index("ix_bullhorn_environment_key", table_name="bullhorn_environment")
    op.drop_table("bullhorn_environment")
