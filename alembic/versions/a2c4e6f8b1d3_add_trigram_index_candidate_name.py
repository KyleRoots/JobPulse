"""Add trigram index on candidate_vetting_log.candidate_name

Revision ID: a2c4e6f8b1d3
Revises: f1a3b5c7d9e2
Create Date: 2026-03-11 17:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a2c4e6f8b1d3'
down_revision = 'f1a3b5c7d9e2'
branch_labels = None
depends_on = None


def upgrade():
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_vetting_log_candidate_name_trgm "
        "ON candidate_vetting_log USING gin (candidate_name gin_trgm_ops)"
    )


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_vetting_log_candidate_name_trgm")
