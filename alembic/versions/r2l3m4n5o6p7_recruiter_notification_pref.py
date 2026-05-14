"""add recruiter_notification_pref table

Revision ID: r2l3m4n5o6p7
Revises: q1k2l3m4n5o6
Create Date: 2026-05-14

May 2026 — per-recruiter-per-job notification opt-out toggle.

Backs the new "📍 Email me about location-review matches" toggle on
the Scout Screening dashboard. Designed extensibly: `notification_type`
column lets us add more toggle kinds later (prestige, threshold, etc.)
without a schema change.

Default behavior is ON for every (user, job, notification_type) combo
when no row exists — so the table only ever carries explicit OFF
records, keeping it small even at scale.

Downgrade drops the table.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = 'r2l3m4n5o6p7'
down_revision = 'q1k2l3m4n5o6'
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
    # table at workflow boot. Skip cleanly so the migration can be stamped.
    if _table_exists('recruiter_notification_pref'):
        if not _index_exists('recruiter_notification_pref', 'ix_recruiter_notif_pref_lookup'):
            op.create_index(
                'ix_recruiter_notif_pref_lookup', 'recruiter_notification_pref',
                ['bullhorn_job_id', 'notification_type'],
            )
        return
    op.create_table(
        'recruiter_notification_pref',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('bullhorn_job_id', sa.Integer(), nullable=False),
        sa.Column('notification_type', sa.String(length=64), nullable=False, server_default='location_review'),
        sa.Column('enabled', sa.Boolean(), nullable=False, server_default='true'),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['user.id'], name='fk_recruiter_notif_pref_user'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id', 'bullhorn_job_id', 'notification_type',
                            name='uq_recruiter_notif_pref'),
    )
    op.create_index(
        'ix_recruiter_notif_pref_lookup', 'recruiter_notification_pref',
        ['bullhorn_job_id', 'notification_type'],
    )
    op.create_index(
        'ix_recruiter_notif_pref_user', 'recruiter_notification_pref', ['user_id'],
    )


def downgrade() -> None:
    if _index_exists('recruiter_notification_pref', 'ix_recruiter_notif_pref_user'):
        op.drop_index('ix_recruiter_notif_pref_user', table_name='recruiter_notification_pref')
    if _index_exists('recruiter_notification_pref', 'ix_recruiter_notif_pref_lookup'):
        op.drop_index('ix_recruiter_notif_pref_lookup', table_name='recruiter_notification_pref')
    if _table_exists('recruiter_notification_pref'):
        op.drop_table('recruiter_notification_pref')
