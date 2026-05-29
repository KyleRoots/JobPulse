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
