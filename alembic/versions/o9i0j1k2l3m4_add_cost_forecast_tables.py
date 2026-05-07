"""add cost_forecast_override + cost_forecast_scenario tables

Revision ID: o9i0j1k2l3m4
Revises: n8h9i0j1k2l3
Create Date: 2026-05-07

Backs the Module-Based AI Cost Forecaster at /admin/ai-cost/forecast.

Schema:
  cost_forecast_override
    module_key      VARCHAR(40)  PRIMARY KEY
    unit_cost_usd   NUMERIC(12,6) NOT NULL DEFAULT 0
    note            VARCHAR(200) NULL
    updated_at      TIMESTAMP NOT NULL
    updated_by      VARCHAR(80) NULL

  cost_forecast_scenario
    id              SERIAL PRIMARY KEY
    name            VARCHAR(120) NOT NULL UNIQUE
    description     VARCHAR(400) NULL
    payload         JSON NOT NULL  ({module_key: monthly_volume})
    created_at      TIMESTAMP NOT NULL  (indexed)
    created_by      VARCHAR(80) NULL
"""
from alembic import op
import sqlalchemy as sa


revision = 'o9i0j1k2l3m4'
down_revision = 'n8h9i0j1k2l3'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'cost_forecast_override',
        sa.Column('module_key', sa.String(length=40), nullable=False),
        sa.Column('unit_cost_usd', sa.Numeric(precision=12, scale=6),
                  nullable=False, server_default='0'),
        sa.Column('note', sa.String(length=200), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by', sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint('module_key'),
    )
    op.create_table(
        'cost_forecast_scenario',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('name', sa.String(length=120), nullable=False),
        sa.Column('description', sa.String(length=400), nullable=True),
        sa.Column('payload', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by', sa.String(length=80), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name'),
    )
    op.create_index('ix_cost_forecast_scenario_created',
                    'cost_forecast_scenario', ['created_at'])


def downgrade() -> None:
    op.drop_index('ix_cost_forecast_scenario_created',
                  table_name='cost_forecast_scenario')
    op.drop_table('cost_forecast_scenario')
    op.drop_table('cost_forecast_override')
