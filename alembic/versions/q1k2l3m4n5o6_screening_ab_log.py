"""add screening_ab_log table

Revision ID: q1k2l3m4n5o6
Revises: p0j1k2l3m4n5
Create Date: 2026-05-09

S2 cost-savings batch — Phase A (shadow infra). Adds the
`screening_ab_log` table that backs the shadow-mode A/B for the
gpt-5.4 vs gpt-4.1-mini screening-scoring swap.

Each row records one (candidate × job) scoring comparison: the
production score returned by gpt-5.4 (prod_score) and the shadow
score returned by gpt-4.1-mini (shadow_score). The shadow path
never affects production behavior — it is logged only for offline
analysis at /admin/ai-cost/screening-ab.

Mirrors the EmbeddingABLog pattern (revision p0j1k2l3m4n5) for
consistency. Behavior gated entirely by the env var
`SCREENING_AB_SHADOW_ENABLED` (default off) — applying this
migration creates the table but does not turn shadow on.

Downgrade drops the table.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'q1k2l3m4n5o6'
down_revision = 'p0j1k2l3m4n5'
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
    bind = op.get_bind()
    return inspect(bind).has_table(name)


def _index_exists(table: str, index_name: str) -> bool:
    bind = op.get_bind()
    try:
        return any(ix['name'] == index_name for ix in inspect(bind).get_indexes(table))
    except Exception:
        return False


def upgrade() -> None:
    # Idempotent: app.py's db.create_all() may have already created this
    # table at workflow boot (it picks up the model definition immediately).
    # Skip cleanly in that case so the migration can still be stamped.
    if _table_exists('screening_ab_log'):
        if not _index_exists('screening_ab_log', 'ix_screening_ab_log_created_at'):
            op.create_index('ix_screening_ab_log_created_at', 'screening_ab_log', ['created_at'])
        if not _index_exists('screening_ab_log', 'ix_screening_ab_log_scores'):
            op.create_index('ix_screening_ab_log_scores', 'screening_ab_log',
                            ['prod_score', 'shadow_score'])
        return
    op.create_table(
        'screening_ab_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('vetting_log_id', sa.Integer(), nullable=True, index=True),
        sa.Column('candidate_job_match_id', sa.Integer(), nullable=True, index=True),
        sa.Column('bullhorn_candidate_id', sa.Integer(), nullable=True, index=True),
        sa.Column('bullhorn_job_id', sa.Integer(), nullable=True, index=True),
        sa.Column('job_title', sa.String(length=500), nullable=True),
        sa.Column('prod_model', sa.String(length=60), nullable=False),
        sa.Column('shadow_model', sa.String(length=60), nullable=False),
        sa.Column('prod_score', sa.Float(), nullable=False),
        sa.Column('shadow_score', sa.Float(), nullable=True),
        sa.Column('score_delta', sa.Float(), nullable=True),
        sa.Column('prod_qualified', sa.Boolean(), nullable=True),
        sa.Column('shadow_qualified_inferred', sa.Boolean(), nullable=True),
        sa.Column('shadow_input_tokens', sa.Integer(), nullable=True),
        sa.Column('shadow_output_tokens', sa.Integer(), nullable=True),
        sa.Column('shadow_estimated_cost_usd', sa.Numeric(12, 6), nullable=True),
        sa.Column('shadow_duration_ms', sa.Integer(), nullable=True),
        sa.Column('shadow_error', sa.String(length=500), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_screening_ab_log_created_at', 'screening_ab_log', ['created_at'])
    op.create_index('ix_screening_ab_log_scores', 'screening_ab_log',
                    ['prod_score', 'shadow_score'])


def downgrade() -> None:
    if _index_exists('screening_ab_log', 'ix_screening_ab_log_scores'):
        op.drop_index('ix_screening_ab_log_scores', table_name='screening_ab_log')
    if _index_exists('screening_ab_log', 'ix_screening_ab_log_created_at'):
        op.drop_index('ix_screening_ab_log_created_at', table_name='screening_ab_log')
    if _table_exists('screening_ab_log'):
        op.drop_table('screening_ab_log')
