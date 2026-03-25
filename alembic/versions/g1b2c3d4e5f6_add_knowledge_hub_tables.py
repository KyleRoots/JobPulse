"""Add Knowledge Hub tables for Scout Support self-learning

Creates:
- knowledge_document table (uploaded docs and ticket-learned resolutions)
- knowledge_entry table (chunked knowledge with embeddings)

Revision ID: g1b2c3d4e5f6
Revises: f1a3b5c7d9e2
Create Date: 2026-03-25 04:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision: str = 'g1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = 'f1a3b5c7d9e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'knowledge_document' not in existing_tables:
        op.create_table(
            'knowledge_document',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('title', sa.String(500), nullable=False),
            sa.Column('filename', sa.String(255), nullable=True),
            sa.Column('doc_type', sa.String(50), nullable=False, server_default='uploaded'),
            sa.Column('category', sa.String(100), nullable=True),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('source_ticket_id', sa.Integer(), sa.ForeignKey('support_ticket.id'), nullable=True),
            sa.Column('raw_text', sa.Text(), nullable=True),
            sa.Column('status', sa.String(20), nullable=False, server_default='active'),
            sa.Column('uploaded_by', sa.String(255), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
            sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index('idx_knowledge_doc_status', 'knowledge_document', ['status'])
        op.create_index('idx_knowledge_doc_type', 'knowledge_document', ['doc_type'])
        op.create_index('ix_knowledge_document_category', 'knowledge_document', ['category'])

    if 'knowledge_entry' not in existing_tables:
        op.create_table(
            'knowledge_entry',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('document_id', sa.Integer(), sa.ForeignKey('knowledge_document.id'), nullable=False),
            sa.Column('chunk_index', sa.Integer(), nullable=False, server_default='0'),
            sa.Column('content', sa.Text(), nullable=False),
            sa.Column('content_hash', sa.String(64), nullable=True),
            sa.Column('embedding_vector', sa.Text(), nullable=True),
            sa.Column('embedding_model', sa.String(50), nullable=True, server_default='text-embedding-3-large'),
            sa.Column('metadata_json', sa.Text(), nullable=True),
            sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.func.now()),
        )
        op.create_index('ix_knowledge_entry_document_id', 'knowledge_entry', ['document_id'])
        op.create_index('ix_knowledge_entry_content_hash', 'knowledge_entry', ['content_hash'])
        op.create_index('idx_knowledge_entry_doc_chunk', 'knowledge_entry', ['document_id', 'chunk_index'])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = inspect(conn)
    existing_tables = inspector.get_table_names()

    if 'knowledge_entry' in existing_tables:
        op.drop_table('knowledge_entry')
    if 'knowledge_document' in existing_tables:
        op.drop_table('knowledge_document')
