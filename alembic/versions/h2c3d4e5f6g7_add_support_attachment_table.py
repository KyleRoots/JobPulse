"""Add SupportAttachment table for ticket file storage

Creates:
- support_attachment table (binary file data, filename, content_type, size, ticket FK)

Revision ID: h2c3d4e5f6g7
Revises: g1b2c3d4e5f6
Create Date: 2026-03-31 15:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'h2c3d4e5f6g7'
down_revision: Union[str, Sequence[str], None] = 'g1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'support_attachment' not in existing_tables:
        op.create_table(
            'support_attachment',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('ticket_id', sa.Integer(), nullable=False),
            sa.Column('filename', sa.String(255), nullable=False),
            sa.Column('content_type', sa.String(100), nullable=False),
            sa.Column('file_size', sa.Integer(), nullable=True),
            sa.Column('file_data', sa.LargeBinary(), nullable=False),
            sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=True),
            sa.ForeignKeyConstraint(['ticket_id'], ['support_ticket.id'], name='fk_support_attachment_ticket_id'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_support_attachment_ticket_id', 'support_attachment', ['ticket_id'])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'support_attachment' in existing_tables:
        op.drop_index('ix_support_attachment_ticket_id', table_name='support_attachment')
        op.drop_table('support_attachment')
