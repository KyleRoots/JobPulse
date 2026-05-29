"""add candidate_fraud_assessment table

Revision ID: u5p6q7r8s9t0
Revises: t4n5o6p7q8r9
Create Date: 2026-05-29

Audit trail for the Phase 1 fraud / fake-candidate detection layer. One row per
assessment — risk score, band, the signals that fired, and whether a Bullhorn
note was written. Advisory-only; never blocks screening.
"""
from alembic import op
import sqlalchemy as sa


revision = "u5p6q7r8s9t0"
down_revision = "t4n5o6p7q8r9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_fraud_assessment",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("bullhorn_candidate_id", sa.Integer(), nullable=True),
        sa.Column("vetting_log_id", sa.Integer(), nullable=True),
        sa.Column("candidate_name", sa.String(length=200), nullable=True),
        sa.Column("candidate_email", sa.String(length=255), nullable=True),
        sa.Column("risk_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("risk_band", sa.String(length=20), nullable=False,
                  server_default="clear"),
        sa.Column("signals_json", sa.Text(), nullable=True),
        sa.Column("trigger", sa.String(length=20), nullable=False,
                  server_default="screening"),
        sa.Column("note_created", sa.Boolean(), nullable=False,
                  server_default=sa.false()),
        sa.Column("bullhorn_note_id", sa.Integer(), nullable=True),
        sa.Column("evaluation_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_candidate_fraud_assessment_created_at",
        "candidate_fraud_assessment", ["created_at"],
    )
    op.create_index(
        "ix_candidate_fraud_assessment_bullhorn_candidate_id",
        "candidate_fraud_assessment", ["bullhorn_candidate_id"],
    )
    op.create_index(
        "ix_candidate_fraud_assessment_vetting_log_id",
        "candidate_fraud_assessment", ["vetting_log_id"],
    )
    op.create_index(
        "ix_candidate_fraud_assessment_cand_created",
        "candidate_fraud_assessment", ["bullhorn_candidate_id", "created_at"],
    )
    op.create_index(
        "ix_candidate_fraud_assessment_band_created",
        "candidate_fraud_assessment", ["risk_band", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_candidate_fraud_assessment_band_created",
                  table_name="candidate_fraud_assessment")
    op.drop_index("ix_candidate_fraud_assessment_cand_created",
                  table_name="candidate_fraud_assessment")
    op.drop_index("ix_candidate_fraud_assessment_vetting_log_id",
                  table_name="candidate_fraud_assessment")
    op.drop_index("ix_candidate_fraud_assessment_bullhorn_candidate_id",
                  table_name="candidate_fraud_assessment")
    op.drop_index("ix_candidate_fraud_assessment_created_at",
                  table_name="candidate_fraud_assessment")
    op.drop_table("candidate_fraud_assessment")
