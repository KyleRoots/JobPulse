"""add user.location_review_emails_enabled

Revision ID: c9d1e3f5a7b9
Revises: 0523f094143b
Create Date: 2026-09-22

Per-user Location Review email kill-switch. Default TRUE; Adam Gebara
seeded OFF at boot via seed_location_review_email_defaults.
"""
from alembic import op
import sqlalchemy as sa


revision = 'c9d1e3f5a7b9'
down_revision = 'b4d6f8h0j2l4'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c['name'] for c in insp.get_columns('user')}
    if 'location_review_emails_enabled' not in cols:
        op.add_column(
            'user',
            sa.Column(
                'location_review_emails_enabled',
                sa.Boolean(),
                nullable=False,
                server_default='true',
            ),
        )


def downgrade():
    conn = op.get_bind()
    insp = sa.inspect(conn)
    cols = {c['name'] for c in insp.get_columns('user')}
    if 'location_review_emails_enabled' in cols:
        op.drop_column('user', 'location_review_emails_enabled')
