"""Add technical_score to candidate_job_match

Revision ID: f1a3b5c7d9e2
Revises: e9f2b4a71d53
Create Date: 2026-03-11 14:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f1a3b5c7d9e2'
down_revision = 'e9f2b4a71d53'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('candidate_job_match', sa.Column('technical_score', sa.Float(), nullable=True))


def downgrade():
    op.drop_column('candidate_job_match', 'technical_score')
