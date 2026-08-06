"""Fraud notifier differentiators: PDF fingerprints, contact cache, calibration.

Revision ID: c8d9e0f1a2b3
Revises: b4c5d6e7f8a9
Create Date: 2026-07-28

Adds:
  * resume_document_fingerprint — PDF Author/Creator/Producer signatures
  * contact_validation_cache — hashed NeverBounce/Twilio results
  * calibration_label/notes/labeled_at on candidate_fraud_assessment
"""
from alembic import op
import sqlalchemy as sa


revision = "c8d9e0f1a2b3"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "resume_document_fingerprint",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("signature", sa.String(length=240), nullable=False),
        sa.Column("author", sa.String(length=200), nullable=True),
        sa.Column("creator", sa.String(length=200), nullable=True),
        sa.Column("producer", sa.String(length=200), nullable=True),
        sa.Column("mod_date", sa.String(length=64), nullable=True),
        sa.Column("content_md5", sa.String(length=32), nullable=True),
        sa.Column("bullhorn_candidate_id", sa.Integer(), nullable=True),
        sa.Column("candidate_name", sa.String(length=200), nullable=True),
        sa.Column("vetting_log_id", sa.Integer(), nullable=True),
        sa.Column("environment_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["environment_id"], ["bullhorn_environment.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_resume_document_fingerprint_created_at",
        "resume_document_fingerprint", ["created_at"],
    )
    op.create_index(
        "ix_resume_document_fingerprint_signature",
        "resume_document_fingerprint", ["signature"],
    )
    op.create_index(
        "ix_resume_document_fingerprint_bullhorn_candidate_id",
        "resume_document_fingerprint", ["bullhorn_candidate_id"],
    )
    op.create_index(
        "ix_resume_document_fingerprint_environment_id",
        "resume_document_fingerprint", ["environment_id"],
    )

    op.create_table(
        "contact_validation_cache",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.Column("contact_type", sa.String(length=16), nullable=False),
        sa.Column("contact_hash", sa.String(length=64), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "contact_type", "contact_hash",
            name="uq_contact_validation_type_hash",
        ),
    )
    op.create_index(
        "ix_contact_validation_cache_expires_at",
        "contact_validation_cache", ["expires_at"],
    )

    op.add_column(
        "candidate_fraud_assessment",
        sa.Column("calibration_label", sa.String(length=20), nullable=True),
    )
    op.add_column(
        "candidate_fraud_assessment",
        sa.Column("calibration_notes", sa.Text(), nullable=True),
    )
    op.add_column(
        "candidate_fraud_assessment",
        sa.Column("calibration_labeled_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_candidate_fraud_assessment_calibration_label",
        "candidate_fraud_assessment", ["calibration_label"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_fraud_assessment_calibration_label",
        table_name="candidate_fraud_assessment",
    )
    op.drop_column("candidate_fraud_assessment", "calibration_labeled_at")
    op.drop_column("candidate_fraud_assessment", "calibration_notes")
    op.drop_column("candidate_fraud_assessment", "calibration_label")
    op.drop_index(
        "ix_contact_validation_cache_expires_at",
        table_name="contact_validation_cache",
    )
    op.drop_table("contact_validation_cache")
    op.drop_index(
        "ix_resume_document_fingerprint_environment_id",
        table_name="resume_document_fingerprint",
    )
    op.drop_index(
        "ix_resume_document_fingerprint_bullhorn_candidate_id",
        table_name="resume_document_fingerprint",
    )
    op.drop_index(
        "ix_resume_document_fingerprint_signature",
        table_name="resume_document_fingerprint",
    )
    op.drop_index(
        "ix_resume_document_fingerprint_created_at",
        table_name="resume_document_fingerprint",
    )
    op.drop_table("resume_document_fingerprint")
