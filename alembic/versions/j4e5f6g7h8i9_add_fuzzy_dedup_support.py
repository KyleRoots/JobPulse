"""Add AI fuzzy duplicate matcher support (Task #57)

Adds:
- ``candidate_merge_log.match_type`` column with default 'exact'.
  Distinguishes legacy exact-field merges from new AI-fuzzy merges in the
  recruiter audit trail.
- ``candidate_profile_embedding`` table caching one embedding per candidate
  built from name + work history + skills + location + education.
- ``fuzzy_evaluation_queue`` table holding overflow candidates that
  exceeded the per-cycle fuzzy cap, so a sustained-load burst can never
  let a candidate age out of the recent window without being evaluated.

Revision ID: j4e5f6g7h8i9
Revises: 0523f094143b
Create Date: 2026-04-27 14:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'j4e5f6g7h8i9'
down_revision: Union[str, Sequence[str], None] = '0523f094143b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    # 1. Add match_type column to candidate_merge_log (idempotent)
    if 'candidate_merge_log' in existing_tables:
        existing_cols = {c['name'] for c in inspector.get_columns('candidate_merge_log')}
        if 'match_type' not in existing_cols:
            op.add_column(
                'candidate_merge_log',
                sa.Column(
                    'match_type',
                    sa.String(length=20),
                    nullable=False,
                    server_default='exact',
                ),
            )

        existing_indexes = {idx['name'] for idx in inspector.get_indexes('candidate_merge_log')}
        if 'idx_merge_log_match_type' not in existing_indexes:
            op.create_index(
                'idx_merge_log_match_type',
                'candidate_merge_log',
                ['match_type'],
            )

    # 2. Create candidate_profile_embedding cache table (idempotent)
    if 'candidate_profile_embedding' not in existing_tables:
        op.create_table(
            'candidate_profile_embedding',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('bullhorn_candidate_id', sa.Integer(), nullable=False),
            sa.Column('candidate_name', sa.String(length=200), nullable=True),
            sa.Column('profile_hash', sa.String(length=64), nullable=False),
            sa.Column('embedding_vector', sa.Text(), nullable=False),
            sa.Column(
                'embedding_model',
                sa.String(length=50),
                nullable=False,
                server_default='text-embedding-3-large',
            ),
            sa.Column('profile_text_snippet', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=True),
            sa.Column('updated_at', sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('bullhorn_candidate_id', name='uq_cand_profile_emb_candidate_id'),
        )
        op.create_index(
            'ix_candidate_profile_embedding_bullhorn_candidate_id',
            'candidate_profile_embedding',
            ['bullhorn_candidate_id'],
        )
        op.create_index(
            'idx_cand_profile_emb_updated',
            'candidate_profile_embedding',
            ['updated_at'],
        )

    # 3. Create fuzzy_evaluation_queue (idempotent) — holds deferred
    #    overflow candidates so the next cycle drains them first.
    if 'fuzzy_evaluation_queue' not in existing_tables:
        op.create_table(
            'fuzzy_evaluation_queue',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('bullhorn_candidate_id', sa.Integer(), nullable=False),
            sa.Column(
                'enqueued_at',
                sa.DateTime(),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                'attempts',
                sa.Integer(),
                nullable=False,
                server_default='0',
            ),
            sa.Column('last_attempted_at', sa.DateTime(), nullable=True),
            sa.Column('last_error', sa.Text(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint(
                'bullhorn_candidate_id',
                name='uq_fuzzy_queue_candidate_id',
            ),
        )
        op.create_index(
            'ix_fuzzy_evaluation_queue_bullhorn_candidate_id',
            'fuzzy_evaluation_queue',
            ['bullhorn_candidate_id'],
        )
        op.create_index(
            'idx_fuzzy_queue_enqueued_at',
            'fuzzy_evaluation_queue',
            ['enqueued_at'],
        )


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'fuzzy_evaluation_queue' in existing_tables:
        existing_indexes = {
            idx['name'] for idx in inspector.get_indexes('fuzzy_evaluation_queue')
        }
        if 'idx_fuzzy_queue_enqueued_at' in existing_indexes:
            op.drop_index(
                'idx_fuzzy_queue_enqueued_at',
                table_name='fuzzy_evaluation_queue',
            )
        if 'ix_fuzzy_evaluation_queue_bullhorn_candidate_id' in existing_indexes:
            op.drop_index(
                'ix_fuzzy_evaluation_queue_bullhorn_candidate_id',
                table_name='fuzzy_evaluation_queue',
            )
        op.drop_table('fuzzy_evaluation_queue')

    if 'candidate_profile_embedding' in existing_tables:
        existing_indexes = {idx['name'] for idx in inspector.get_indexes('candidate_profile_embedding')}
        if 'idx_cand_profile_emb_updated' in existing_indexes:
            op.drop_index('idx_cand_profile_emb_updated', table_name='candidate_profile_embedding')
        if 'ix_candidate_profile_embedding_bullhorn_candidate_id' in existing_indexes:
            op.drop_index(
                'ix_candidate_profile_embedding_bullhorn_candidate_id',
                table_name='candidate_profile_embedding',
            )
        op.drop_table('candidate_profile_embedding')

    if 'candidate_merge_log' in existing_tables:
        existing_indexes = {idx['name'] for idx in inspector.get_indexes('candidate_merge_log')}
        if 'idx_merge_log_match_type' in existing_indexes:
            op.drop_index('idx_merge_log_match_type', table_name='candidate_merge_log')

        existing_cols = {c['name'] for c in inspector.get_columns('candidate_merge_log')}
        if 'match_type' in existing_cols:
            op.drop_column('candidate_merge_log', 'match_type')
