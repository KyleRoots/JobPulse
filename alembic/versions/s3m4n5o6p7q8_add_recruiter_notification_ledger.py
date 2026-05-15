"""add recruiter_notification_ledger table

Revision ID: s3m4n5o6p7q8
Revises: r2l3m4n5o6p7
Create Date: 2026-05-15

Task #95 — Stop duplicate recruiter emails on auditor re-vet.

The Quality Auditor's clear_candidate_vetting_state cascade deletes the
CandidateJobMatch rows that previously carried notification_sent=True,
so the next vetting cycle creates fresh matches with
notification_sent=False and the recruiter notification path has no
idempotency signal — it would fire a second email even though the
Bullhorn note path correctly skips the duplicate via its own check.

This ledger lives outside the auditor cascade by design: it is keyed
on (bullhorn_candidate_id, bullhorn_job_id, notification_type) rather
than vetting_log_id, so re-vets that wipe vetting logs do not wipe
the dedupe signal.
"""
from alembic import op
import sqlalchemy as sa


revision = 's3m4n5o6p7q8'
down_revision = 'r2l3m4n5o6p7'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'recruiter_notification_ledger',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('bullhorn_candidate_id', sa.Integer(), nullable=False),
        sa.Column('bullhorn_job_id', sa.Integer(), nullable=False),
        sa.Column(
            'notification_type', sa.String(length=64),
            nullable=False, server_default='qualified',
        ),
        sa.Column('sent_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint(
            'bullhorn_candidate_id', 'bullhorn_job_id', 'notification_type',
            name='uq_recruiter_notification_ledger',
        ),
    )
    op.create_index(
        'ix_recruiter_notification_ledger_bullhorn_candidate_id',
        'recruiter_notification_ledger', ['bullhorn_candidate_id'],
    )
    op.create_index(
        'ix_recruiter_notification_ledger_bullhorn_job_id',
        'recruiter_notification_ledger', ['bullhorn_job_id'],
    )
    op.create_index(
        'ix_recruiter_notification_ledger_notification_type',
        'recruiter_notification_ledger', ['notification_type'],
    )
    op.create_index(
        'ix_recruiter_notification_ledger_sent_at',
        'recruiter_notification_ledger', ['sent_at'],
    )


def downgrade() -> None:
    op.drop_index(
        'ix_recruiter_notification_ledger_sent_at',
        table_name='recruiter_notification_ledger',
    )
    op.drop_index(
        'ix_recruiter_notification_ledger_notification_type',
        table_name='recruiter_notification_ledger',
    )
    op.drop_index(
        'ix_recruiter_notification_ledger_bullhorn_job_id',
        table_name='recruiter_notification_ledger',
    )
    op.drop_index(
        'ix_recruiter_notification_ledger_bullhorn_candidate_id',
        table_name='recruiter_notification_ledger',
    )
    op.drop_table('recruiter_notification_ledger')
