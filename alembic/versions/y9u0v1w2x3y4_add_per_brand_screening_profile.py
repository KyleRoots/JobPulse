"""add per-brand screening profile + config overrides to bullhorn_environment

Revision ID: y9u0v1w2x3y4
Revises: x8t9u0v1w2x3
Create Date: 2026-06-12

Per-Brand Screening Profiles (Task #101). Adds two nullable columns to the
bullhorn_environment registry:

  screening_profile          — VARCHAR(50), selects screening prompt/scoring
                               tuning. NULL resolves to 'standard' (the
                               historical IT behavior), so the default (Myticas)
                               environment is unchanged.
  screening_config_overrides — TEXT (JSON), optional per-environment
                               VettingConfig key→value overrides layered on top
                               of the global settings.

Both columns are nullable with no backfill — the default environment leaves them
NULL and behaves byte-for-byte as before. Boot-time idempotent ALTERs in
seeding/migrations.py cover dev DBs; this keeps production schema in parity.
"""
from alembic import op
import sqlalchemy as sa


revision = "y9u0v1w2x3y4"
down_revision = "x8t9u0v1w2x3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "bullhorn_environment",
        sa.Column("screening_profile", sa.String(length=50), nullable=True),
    )
    op.add_column(
        "bullhorn_environment",
        sa.Column("screening_config_overrides", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("bullhorn_environment", "screening_config_overrides")
    op.drop_column("bullhorn_environment", "screening_profile")
