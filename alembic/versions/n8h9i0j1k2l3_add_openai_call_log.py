"""add openai_call_log table for AI cost telemetry

Revision ID: n8h9i0j1k2l3
Revises: m7g8h9i0j1k2
Create Date: 2026-05-07

Creates the `openai_call_log` table backing the AI cost telemetry feature
introduced by Task #87. One row per OpenAI API invocation across the
platform; populated fire-and-forget by `services.openai_helper.log_call()`
and consumed by the `/admin/ai-cost` dashboard plus the `tile_ai_cost_24h`
System Health tile.

Schema:
  id                   BIGSERIAL PRIMARY KEY
  created_at           TIMESTAMP NOT NULL  (indexed)
  call_site_id         VARCHAR(80) NOT NULL  (indexed)
  model                VARCHAR(80) NOT NULL
  input_tokens         INTEGER NOT NULL DEFAULT 0
  output_tokens        INTEGER NOT NULL DEFAULT 0
  cached_input_tokens  INTEGER NOT NULL DEFAULT 0
  estimated_cost_usd   NUMERIC(12,6) NOT NULL DEFAULT 0
  duration_ms          INTEGER NULL
  tenant_id            VARCHAR(80) NULL  (indexed)
  customer_id          VARCHAR(80) NULL  (indexed)
  entity_type          VARCHAR(40) NULL
  entity_id            VARCHAR(80) NULL
  success              BOOLEAN NOT NULL DEFAULT TRUE
  error_type           VARCHAR(120) NULL

Indexes:
  - Single column on created_at, call_site_id, tenant_id, customer_id
    (auto-created by `index=True` in the model)
  - Composite (call_site_id, created_at) for per-site time-window rollups
  - Composite (tenant_id, created_at) for per-tenant rollups
  - Composite (customer_id, created_at) for per-customer rollups (powers
    the future per-customer cost-forecasting dashboard)
"""
from alembic import op
import sqlalchemy as sa


revision = 'n8h9i0j1k2l3'
down_revision = 'm7g8h9i0j1k2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'openai_call_log',
        sa.Column('id', sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('call_site_id', sa.String(length=80), nullable=False),
        sa.Column('model', sa.String(length=80), nullable=False),
        sa.Column('input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('output_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('cached_input_tokens', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('estimated_cost_usd', sa.Numeric(precision=12, scale=6), nullable=False, server_default='0'),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('tenant_id', sa.String(length=80), nullable=True),
        sa.Column('customer_id', sa.String(length=80), nullable=True),
        sa.Column('entity_type', sa.String(length=40), nullable=True),
        sa.Column('entity_id', sa.String(length=80), nullable=True),
        sa.Column('success', sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column('error_type', sa.String(length=120), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_openai_call_log_created_at', 'openai_call_log', ['created_at'])
    op.create_index('ix_openai_call_log_call_site_id', 'openai_call_log', ['call_site_id'])
    op.create_index('ix_openai_call_log_tenant_id', 'openai_call_log', ['tenant_id'])
    op.create_index('ix_openai_call_log_customer_id', 'openai_call_log', ['customer_id'])
    op.create_index('ix_openai_call_log_site_created', 'openai_call_log', ['call_site_id', 'created_at'])
    op.create_index('ix_openai_call_log_tenant_created', 'openai_call_log', ['tenant_id', 'created_at'])
    op.create_index('ix_openai_call_log_customer_created', 'openai_call_log', ['customer_id', 'created_at'])


def downgrade() -> None:
    op.drop_index('ix_openai_call_log_customer_created', table_name='openai_call_log')
    op.drop_index('ix_openai_call_log_tenant_created', table_name='openai_call_log')
    op.drop_index('ix_openai_call_log_site_created', table_name='openai_call_log')
    op.drop_index('ix_openai_call_log_customer_id', table_name='openai_call_log')
    op.drop_index('ix_openai_call_log_tenant_id', table_name='openai_call_log')
    op.drop_index('ix_openai_call_log_call_site_id', table_name='openai_call_log')
    op.drop_index('ix_openai_call_log_created_at', table_name='openai_call_log')
    op.drop_table('openai_call_log')
