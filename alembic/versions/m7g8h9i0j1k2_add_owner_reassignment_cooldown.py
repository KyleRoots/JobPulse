"""add owner reassignment cooldown table

Revision ID: m7g8h9i0j1k2
Revises: l6f7g8h9i0j1
Create Date: 2026-04-30

Adds the `owner_reassignment_cooldown` table backing the per-candidate
cooldown bandage that prevents the 5-minute owner-reassignment cycle from
re-paying the Bullhorn-Notes-search cost on the same Pandologic candidates
over and over.

Schema:
  candidate_id        BIGINT  PRIMARY KEY  (Bullhorn candidate id)
  last_evaluated_at   TIMESTAMP NOT NULL   (indexed; cutoff lookups)
  last_outcome        VARCHAR(40) NOT NULL ('no_human_activity',
                                            'already_correct')
  evaluation_count    INTEGER NOT NULL DEFAULT 1

A single covering index on `last_evaluated_at` supports the cutoff filter
used by `_fetch_active_cooldown_ids`. The PK on `candidate_id` is the
conflict target for the `INSERT ... ON CONFLICT` upsert.
"""
from alembic import op
import sqlalchemy as sa

revision = 'm7g8h9i0j1k2'
down_revision = 'l6f7g8h9i0j1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'owner_reassignment_cooldown',
        sa.Column('candidate_id', sa.BigInteger(), primary_key=True,
                  nullable=False),
        sa.Column('last_evaluated_at', sa.DateTime(), nullable=False),
        sa.Column('last_outcome', sa.String(length=40), nullable=False),
        sa.Column('evaluation_count', sa.Integer(), nullable=False,
                  server_default=sa.text('1')),
    )
    op.create_index(
        'ix_owner_reassignment_cooldown_last_evaluated_at',
        'owner_reassignment_cooldown',
        ['last_evaluated_at'],
        unique=False,
    )


def downgrade():
    op.drop_index(
        'ix_owner_reassignment_cooldown_last_evaluated_at',
        table_name='owner_reassignment_cooldown',
    )
    op.drop_table('owner_reassignment_cooldown')
