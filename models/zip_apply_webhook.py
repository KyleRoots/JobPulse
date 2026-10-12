"""Received ZipRecruiter Apply webhook deliveries.

Receipt rows store ids and presence flags only. Resume bytes and contact
values are not stored here. On Qualified, the live JSON is handed to the
same Bullhorn path as mailbox apply. Past rows cannot be replayed.
"""
from datetime import datetime

from sqlalchemy import BigInteger, Integer

from extensions import db


class ZipApplyDelivery(db.Model):
    __tablename__ = "zip_apply_delivery"

    id = db.Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    job_id = db.Column(db.String(120), nullable=True, index=True)
    response_id = db.Column(db.String(120), nullable=True, index=True)
    body_bytes = db.Column(db.Integer, nullable=False, default=0)
    payload_sha256 = db.Column(db.String(64), nullable=False)
    has_name = db.Column(db.Boolean, nullable=False, default=False)
    has_email = db.Column(db.Boolean, nullable=False, default=False)
    has_phone = db.Column(db.Boolean, nullable=False, default=False)
    has_resume = db.Column(db.Boolean, nullable=False, default=False)
    has_profile = db.Column(db.Boolean, nullable=False, default=False)
    signature_header_present = db.Column(db.Boolean, nullable=False, default=False)
    duplicate_of_id = db.Column(db.Integer, nullable=True)
    field_names = db.Column(db.Text, nullable=True)
    # received: stored only. processed: candidate written. unmatched_job:
    # person written, Zip reference did not map to a Bullhorn job.
    # failed: Bullhorn path did not finish (Zip should retry).
    # duplicate: a prior row for this response already landed.
    ingest_status = db.Column(db.String(32), nullable=True, index=True)
    bullhorn_candidate_id = db.Column(db.Integer, nullable=True)
    bullhorn_submission_id = db.Column(db.Integer, nullable=True)
    ingest_error = db.Column(db.String(500), nullable=True)
