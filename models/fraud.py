"""Fraud-detection audit model.

One row per fraud assessment of a candidate. Powers the recruiter-portal risk
badge and gives a permanent, queryable forensic trail of why a candidate was
flagged (signals + score + band) and whether a Bullhorn note was written.

Advisory-only by design: a row here NEVER blocks or alters screening. It exists
to surface risk for human judgement.
"""
from datetime import datetime
from sqlalchemy import BigInteger, Integer

from extensions import db


class CandidateFraudAssessment(db.Model):
    """One row per fraud assessment attempt against a candidate."""

    __tablename__ = "candidate_fraud_assessment"

    id = db.Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True,
    )
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True,
    )

    # Bullhorn candidate this assessment is for.
    bullhorn_candidate_id = db.Column(db.Integer, nullable=True, index=True)
    # Loosely coupled to the vetting log that triggered this (no FK constraint,
    # consistent with CandidateMergeLog's plain-integer linkage style).
    vetting_log_id = db.Column(db.Integer, nullable=True, index=True)

    # Identity snapshot at assessment time.
    candidate_name = db.Column(db.String(200), nullable=True)
    candidate_email = db.Column(db.String(255), nullable=True)

    # Outcome.
    risk_score = db.Column(db.Integer, nullable=False, default=0)
    # 'clear' | 'review' | 'high_risk'
    risk_band = db.Column(db.String(20), nullable=False, default="clear", index=True)
    # JSON list of signal dicts: [{code,label,points,evidence,details}, ...]
    signals_json = db.Column(db.Text, nullable=True)

    # What kicked off this assessment: 'screening', 'manual', 'backfill'.
    trigger = db.Column(db.String(20), nullable=False, default="screening")

    # Bullhorn note outcome (only attempted on High-Risk when enabled).
    note_created = db.Column(db.Boolean, nullable=False, default=False)
    bullhorn_note_id = db.Column(db.Integer, nullable=True)

    # Captured if the assessment itself errored (fail-soft — never raises).
    evaluation_error = db.Column(db.Text, nullable=True)

    # Weekly calibration label: 'tp' | 'fp' | 'nudge' | 'ignore' (nullable).
    calibration_label = db.Column(db.String(20), nullable=True, index=True)
    calibration_notes = db.Column(db.Text, nullable=True)
    calibration_labeled_at = db.Column(db.DateTime, nullable=True)

    # Multi-tenant discriminator (Task #100): the connected Bullhorn instance
    # this row belongs to. Nullable + backfilled to the default (Myticas)
    # environment so single-tenant behavior is byte-for-byte unchanged.
    environment_id = db.Column(
        db.Integer, db.ForeignKey('bullhorn_environment.id'),
        nullable=True, index=True,
    )

    __table_args__ = (
        db.Index(
            "ix_candidate_fraud_assessment_cand_created",
            "bullhorn_candidate_id", "created_at",
        ),
        db.Index(
            "ix_candidate_fraud_assessment_band_created",
            "risk_band", "created_at",
        ),
    )

    def __repr__(self):
        return (
            f"<CandidateFraudAssessment {self.id}: cand={self.bullhorn_candidate_id} "
            f"{self.risk_band} ({self.risk_score})>"
        )


class ResumeDocumentFingerprint(db.Model):
    """PDF Author/Creator/Producer signature seen on a candidate résumé."""

    __tablename__ = "resume_document_fingerprint"

    id = db.Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True,
    )
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True,
    )
    signature = db.Column(db.String(240), nullable=False, index=True)
    author = db.Column(db.String(200), nullable=True)
    creator = db.Column(db.String(200), nullable=True)
    producer = db.Column(db.String(200), nullable=True)
    mod_date = db.Column(db.String(64), nullable=True)
    content_md5 = db.Column(db.String(32), nullable=True)
    bullhorn_candidate_id = db.Column(db.Integer, nullable=True, index=True)
    candidate_name = db.Column(db.String(200), nullable=True)
    vetting_log_id = db.Column(db.Integer, nullable=True)
    environment_id = db.Column(
        db.Integer, db.ForeignKey('bullhorn_environment.id'),
        nullable=True, index=True,
    )

    def __repr__(self):
        return f"<ResumeDocumentFingerprint {self.id} sig={self.signature[:40]}>"


class ContactValidationCache(db.Model):
    """Cached NeverBounce / Twilio Lookup results (hashed contact keys)."""

    __tablename__ = "contact_validation_cache"

    id = db.Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True, autoincrement=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    # 'email' | 'phone'
    contact_type = db.Column(db.String(16), nullable=False)
    contact_hash = db.Column(db.String(64), nullable=False)
    result_json = db.Column(db.Text, nullable=True)
    expires_at = db.Column(db.DateTime, nullable=True, index=True)

    __table_args__ = (
        db.UniqueConstraint(
            "contact_type", "contact_hash",
            name="uq_contact_validation_type_hash",
        ),
    )

    def __repr__(self):
        return f"<ContactValidationCache {self.contact_type}:{self.contact_hash[:8]}>"

