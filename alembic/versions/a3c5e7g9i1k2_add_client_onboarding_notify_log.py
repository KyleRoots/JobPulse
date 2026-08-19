"""add client_onboarding_notify_log table

Revision ID: a3c5e7g9i1k2
Revises: a9b0c1d2e3f4
Create Date: 2026-08-19

Once-per-company ledger for Myticas client onboarding notify emails.
"""
from alembic import op
import sqlalchemy as sa


revision = "a3c5e7g9i1k2"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "client_onboarding_notify_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("bullhorn_company_id", sa.Integer(), nullable=False),
        sa.Column("company_name", sa.String(length=255), nullable=True),
        sa.Column("company_status", sa.String(length=80), nullable=True),
        sa.Column("company_type", sa.String(length=80), nullable=True),
        sa.Column("trigger_type", sa.String(length=20), nullable=False),
        sa.Column("trigger_entity_id", sa.Integer(), nullable=True),
        sa.Column("sales_rep_name", sa.String(length=255), nullable=True),
        sa.Column("sales_rep_email", sa.String(length=255), nullable=True),
        sa.Column("sales_rep_source", sa.String(length=40), nullable=True),
        sa.Column("intended_to", sa.String(length=255), nullable=True),
        sa.Column("intended_cc", sa.String(length=255), nullable=True),
        sa.Column("actual_to", sa.String(length=255), nullable=True),
        sa.Column("live_mode", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("email_sent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("skip_reason", sa.String(length=120), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("bullhorn_company_id", name="uq_client_ob_notify_company"),
    )
    op.create_index(
        "ix_client_onboarding_notify_log_created_at",
        "client_onboarding_notify_log",
        ["created_at"],
    )
    op.create_index(
        "ix_client_ob_notify_company_created",
        "client_onboarding_notify_log",
        ["bullhorn_company_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_client_ob_notify_company_created",
        table_name="client_onboarding_notify_log",
    )
    op.drop_index(
        "ix_client_onboarding_notify_log_created_at",
        table_name="client_onboarding_notify_log",
    )
    op.drop_table("client_onboarding_notify_log")
