"""add environment_brand, user.environment_id, and per-environment composite uniques

Revision ID: z0a1b2c3d4e5
Revises: y9u0v1w2x3y4
Create Date: 2026-06-12

Multi-tenant backbone (Task #100 Step 4-7 foundation). Three changes:

  1. New `environment_brand` table — apply-form identity (host->template/logo/
     company/from/to) belonging to one environment. A single Bullhorn
     environment may own multiple brands (Myticas + STSI today).
  2. `user.environment_id` — nullable FK to bullhorn_environment. NULL resolves
     to the default (Myticas) environment, so existing users are unaffected.
  3. Swap the legacy global single-column unique for a per-environment composite
     unique on the three isolation tables:
       job_vetting_requirements   (environment_id, bullhorn_job_id)
       candidate_profile_embedding(environment_id, bullhorn_candidate_id)
       job_embedding              (environment_id, bullhorn_job_id)
     A plain index on the scoped column is kept (the model declares it
     index=True only). In single-environment mode the composite behaves exactly
     like the old global unique because every row carries the default env id.

Mirrors the idempotent boot-time ALTERs in seeding/migrations.py so production
(Alembic) and dev (create_all + ALTER) converge on identical schema.
"""
from alembic import op
import sqlalchemy as sa


revision = "z0a1b2c3d4e5"
down_revision = "y9u0v1w2x3y4"
branch_labels = None
depends_on = None


_UNIQUE_SWAPS = (
    # (table, legacy_unique_constraint, plain_index, scoped_column, composite_index)
    ("job_vetting_requirements",
     "job_vetting_requirements_bullhorn_job_id_key",
     "ix_job_vetting_requirements_bullhorn_job_id",
     "bullhorn_job_id", "uq_jvr_env_job"),
    ("candidate_profile_embedding",
     "candidate_profile_embedding_bullhorn_candidate_id_key",
     "ix_candidate_profile_embedding_bullhorn_candidate_id",
     "bullhorn_candidate_id", "uq_cpe_env_candidate"),
    ("job_embedding",
     "job_embedding_bullhorn_job_id_key",
     "ix_job_embedding_bullhorn_job_id",
     "bullhorn_job_id", "uq_je_env_job"),
)


def upgrade() -> None:
    # 1. environment_brand table.
    op.create_table(
        "environment_brand",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("environment_id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("domains", sa.Text(), nullable=True),
        sa.Column("apply_template", sa.String(length=120), nullable=False),
        sa.Column("logo_path", sa.String(length=255), nullable=True),
        sa.Column("logo_filename", sa.String(length=120), nullable=True),
        sa.Column("logo_cid", sa.String(length=120), nullable=True),
        sa.Column("company_name", sa.String(length=160), nullable=True),
        sa.Column("logo_alt_text", sa.String(length=160), nullable=True),
        sa.Column("from_email", sa.String(length=255), nullable=True),
        sa.Column("to_email", sa.String(length=255), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["environment_id"], ["bullhorn_environment.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("key"),
    )
    op.create_index(
        "ix_environment_brand_environment_id", "environment_brand",
        ["environment_id"],
    )
    op.create_index(
        "ix_environment_brand_key", "environment_brand", ["key"],
    )
    op.create_index(
        "ix_environment_brand_is_default", "environment_brand", ["is_default"],
    )
    op.create_index(
        "ix_environment_brand_is_active", "environment_brand", ["is_active"],
    )
    # At most one default brand.
    op.create_index(
        "uq_environment_brand_single_default", "environment_brand",
        ["is_default"], unique=True,
        postgresql_where=sa.text("is_default"),
    )

    # 2. user.environment_id.
    op.add_column(
        "user", sa.Column("environment_id", sa.Integer(), nullable=True),
    )
    op.create_index("ix_user_environment_id", "user", ["environment_id"])
    op.create_foreign_key(
        "fk_user_environment_id", "user", "bullhorn_environment",
        ["environment_id"], ["id"],
    )

    # 3. Per-environment composite uniques.
    for table, legacy_uq, plain_idx, col, composite_uq in _UNIQUE_SWAPS:
        op.execute(f'ALTER TABLE {table} DROP CONSTRAINT IF EXISTS {legacy_uq}')
        op.execute(f'CREATE INDEX IF NOT EXISTS {plain_idx} ON {table} ({col})')
        op.execute(
            f'CREATE UNIQUE INDEX IF NOT EXISTS {composite_uq} '
            f'ON {table} (environment_id, {col})'
        )


def downgrade() -> None:
    # Reverse the unique swaps: drop composite, restore global single-col unique.
    for table, legacy_uq, plain_idx, col, composite_uq in _UNIQUE_SWAPS:
        op.execute(f'DROP INDEX IF EXISTS {composite_uq}')
        op.execute(f'DROP INDEX IF EXISTS {plain_idx}')
        op.execute(
            f'ALTER TABLE {table} ADD CONSTRAINT {legacy_uq} UNIQUE ({col})'
        )

    op.drop_constraint("fk_user_environment_id", "user", type_="foreignkey")
    op.drop_index("ix_user_environment_id", table_name="user")
    op.drop_column("user", "environment_id")

    op.drop_index("uq_environment_brand_single_default", table_name="environment_brand")
    op.drop_index("ix_environment_brand_is_active", table_name="environment_brand")
    op.drop_index("ix_environment_brand_is_default", table_name="environment_brand")
    op.drop_index("ix_environment_brand_key", table_name="environment_brand")
    op.drop_index("ix_environment_brand_environment_id", table_name="environment_brand")
    op.drop_table("environment_brand")
