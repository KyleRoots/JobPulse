"""Received ZipRecruiter Apply webhook deliveries.

The first version only records that a JSON delivery arrived and which
fields were present. It does not create Bullhorn candidates. Resume
bytes are not stored.
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
