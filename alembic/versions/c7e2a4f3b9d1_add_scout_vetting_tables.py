"""Add Scout Vetting tables and scout_vetting_enabled column

Creates:
- scout_vetting_session table (conversational AI vetting per candidate+job)
- vetting_conversation_turn table (individual email exchanges)
- scout_vetting_enabled column on job_vetting_requirements (per-job override)

Revision ID: c7e2a4f3b9d1
Revises: b3f8a2d91e47
Create Date: 2026-02-18 17:35:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


# revision identifiers, used by Alembic.
revision: str = 'c7e2a4f3b9d1'
down_revision: Union[str, Sequence[str], None] = 'b3f8a2d91e47'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create Scout Vetting tables and add per-job toggle column."""
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    # --- scout_vetting_session ---
    if 'scout_vetting_session' not in existing_tables:
        op.create_table(
            'scout_vetting_session',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('vetting_log_id', sa.Integer(), sa.ForeignKey('candidate_vetting_log.id'), nullable=False),
            sa.Column('candidate_job_match_id', sa.Integer(), sa.ForeignKey('candidate_job_match.id'), nullable=True),

            # Candidate info (denormalized)
            sa.Column('bullhorn_candidate_id', sa.Integer(), nullable=False),
            sa.Column('candidate_email', sa.String(255), nullable=False),
            sa.Column('candidate_name', sa.String(255), nullable=True),

            # Job info (denormalized)
            sa.Column('bullhorn_job_id', sa.Integer(), nullable=False),
            sa.Column('job_title', sa.String(500), nullable=True),

            # Recruiter info
            sa.Column('recruiter_email', sa.String(255), nullable=True),
            sa.Column('recruiter_name', sa.String(255), nullable=True),

            # Session state
            sa.Column('status', sa.String(50), nullable=False, server_default='pending'),

            # Vetting content
            sa.Column('vetting_questions_json', sa.Text(), nullable=True),
            sa.Column('answered_questions_json', sa.Text(), nullable=True),
            sa.Column('current_turn', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('max_turns', sa.Integer(), nullable=False, server_default='5'),

            # Email cadence
            sa.Column('last_outreach_at', sa.DateTime(), nullable=True),
            sa.Column('last_reply_at', sa.DateTime(), nullable=True),
            sa.Column('follow_up_count', sa.Integer(), nullable=False, server_default='0'),

            # Outcome
            sa.Column('outcome_summary', sa.Text(), nullable=True),
            sa.Column('outcome_score', sa.Float(), nullable=True),

            # Bullhorn integration
            sa.Column('bullhorn_note_id', sa.Integer(), nullable=True),
            sa.Column('note_created', sa.Boolean(), nullable=False, server_default='false'),
            sa.Column('handoff_sent', sa.Boolean(), nullable=False, server_default='false'),

            # Email threading
            sa.Column('last_message_id', sa.String(255), nullable=True),

            # Timestamps
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

        # Individual column indexes
        op.create_index('ix_svs_vetting_log_id', 'scout_vetting_session', ['vetting_log_id'])
        op.create_index('ix_svs_candidate_job_match_id', 'scout_vetting_session', ['candidate_job_match_id'])
        op.create_index('ix_svs_bullhorn_candidate_id', 'scout_vetting_session', ['bullhorn_candidate_id'])
        op.create_index('ix_svs_bullhorn_job_id', 'scout_vetting_session', ['bullhorn_job_id'])

        # Composite indexes for common queries
        op.create_index('idx_svs_candidate_job_status', 'scout_vetting_session',
                        ['bullhorn_candidate_id', 'bullhorn_job_id', 'status'])
        op.create_index('idx_svs_status_outreach', 'scout_vetting_session',
                        ['status', 'last_outreach_at'])
        op.create_index('idx_svs_status_updated', 'scout_vetting_session',
                        ['status', 'updated_at'])

    # --- vetting_conversation_turn ---
    if 'vetting_conversation_turn' not in existing_tables:
        op.create_table(
            'vetting_conversation_turn',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('session_id', sa.Integer(), sa.ForeignKey('scout_vetting_session.id'), nullable=False),
            sa.Column('turn_number', sa.Integer(), nullable=False),
            sa.Column('direction', sa.String(10), nullable=False),

            # Email content
            sa.Column('email_subject', sa.String(500), nullable=True),
            sa.Column('email_body', sa.Text(), nullable=True),

            # AI analysis
            sa.Column('ai_intent', sa.String(50), nullable=True),
            sa.Column('ai_reasoning', sa.Text(), nullable=True),
            sa.Column('questions_asked_json', sa.Text(), nullable=True),
            sa.Column('answers_extracted_json', sa.Text(), nullable=True),

            # Email threading
            sa.Column('message_id', sa.String(255), nullable=True),

            # Timestamps
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )

        op.create_index('ix_vct_session_id', 'vetting_conversation_turn', ['session_id'])

    # --- Add scout_vetting_enabled to job_vetting_requirements ---
    if 'job_vetting_requirements' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('job_vetting_requirements')]
        if 'scout_vetting_enabled' not in existing_cols:
            op.add_column('job_vetting_requirements',
                          sa.Column('scout_vetting_enabled', sa.Boolean(), nullable=True))


def downgrade() -> None:
    """Drop Scout Vetting tables and remove per-job toggle column."""
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    # Remove column from job_vetting_requirements
    if 'job_vetting_requirements' in existing_tables:
        existing_cols = [c['name'] for c in inspector.get_columns('job_vetting_requirements')]
        if 'scout_vetting_enabled' in existing_cols:
            op.drop_column('job_vetting_requirements', 'scout_vetting_enabled')

    # Drop conversation turns first (FK dependency)
    if 'vetting_conversation_turn' in existing_tables:
        op.drop_table('vetting_conversation_turn')

    # Drop sessions
    if 'scout_vetting_session' in existing_tables:
        op.drop_table('scout_vetting_session')
