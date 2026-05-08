"""add embedding_ab_log table + bump self_screen_cooldown_minutes 60->120

Revision ID: p0j1k2l3m4n5
Revises: o9i0j1k2l3m4
Create Date: 2026-05-07

Two changes bundled as the Day-0 build for the May 2026 cost-savings
batch (S1 + Phase A shadow infra):

1) `embedding_ab_log` table — backs the shadow-mode A/B for the
   text-embedding-3-large → text-embedding-3-small swap. Each row
   records one (candidate × job) similarity comparison between the
   primary model (currently used for the gate decision) and the
   shadow model (the candidate replacement). Read by
   /admin/ai-cost/embedding-ab to compute concordance, false-negative
   rate, threshold-sweep recommendations, etc.

2) Cooldown bump: `self_screen_cooldown_minutes` VettingConfig value
   is bumped from 60 → 120 IF the existing value is still the seeded
   default of 60. Custom values (anything other than 60) are left
   untouched. This is S1 of the cost-savings queue — extends the
   loop-killer protection window now that we have observability via
   tile_skip_gates.

Downgrade reverses both: drops embedding_ab_log and rolls cooldown
120 → 60 only when current value is 120.
"""
from alembic import op
import sqlalchemy as sa


revision = 'p0j1k2l3m4n5'
down_revision = 'o9i0j1k2l3m4'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- Table: embedding_ab_log -------------------------------------------
    op.create_table(
        'embedding_ab_log',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('vetting_log_id', sa.Integer(), nullable=True, index=True),
        sa.Column('bullhorn_candidate_id', sa.Integer(), nullable=False, index=True),
        sa.Column('candidate_name', sa.String(length=255), nullable=True),
        sa.Column('bullhorn_job_id', sa.Integer(), nullable=False, index=True),
        sa.Column('job_title', sa.String(length=500), nullable=True),
        sa.Column('primary_model', sa.String(length=60), nullable=False),
        sa.Column('shadow_model', sa.String(length=60), nullable=False),
        sa.Column('primary_score', sa.Float(), nullable=False),
        sa.Column('shadow_score', sa.Float(), nullable=False),
        sa.Column('threshold_used', sa.Float(), nullable=False),
        sa.Column('primary_passed', sa.Boolean(), nullable=False),
        sa.Column('shadow_would_pass', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_embedding_ab_log_created_at', 'embedding_ab_log', ['created_at'])
    op.create_index('ix_embedding_ab_log_concordance', 'embedding_ab_log',
                    ['primary_passed', 'shadow_would_pass'])

    # --- Cooldown bump: 60 -> 120 (only if still the seeded default) -------
    op.execute(
        "UPDATE vetting_config "
        "SET setting_value = '120' "
        "WHERE setting_key = 'self_screen_cooldown_minutes' "
        "  AND setting_value = '60'"
    )


def downgrade() -> None:
    # Revert cooldown only if it's currently 120.
    op.execute(
        "UPDATE vetting_config "
        "SET setting_value = '60' "
        "WHERE setting_key = 'self_screen_cooldown_minutes' "
        "  AND setting_value = '120'"
    )
    op.drop_index('ix_embedding_ab_log_concordance', table_name='embedding_ab_log')
    op.drop_index('ix_embedding_ab_log_created_at', table_name='embedding_ab_log')
    op.drop_table('embedding_ab_log')
