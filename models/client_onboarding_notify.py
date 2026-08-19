"""Once-per-company ledger for Myticas client onboarding notify.

Observe-only: no Bullhorn writes. Unique on company id so Finance is
not emailed twice for the same client, ever.
"""
from datetime import datetime
from sqlalchemy import BigInteger, Index, Integer, UniqueConstraint

from extensions import db


class ClientOnboardingNotifyLog(db.Model):
    __tablename__ = "client_onboarding_notify_log"

    id = db.Column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True,
    )

    bullhorn_company_id = db.Column(db.Integer, nullable=False)
    company_name = db.Column(db.String(255), nullable=True)
    company_status = db.Column(db.String(80), nullable=True)
    company_type = db.Column(db.String(80), nullable=True)

    trigger_type = db.Column(db.String(20), nullable=False)  # sendout | interview
    trigger_entity_id = db.Column(db.Integer, nullable=True)

    sales_rep_name = db.Column(db.String(255), nullable=True)
    sales_rep_email = db.Column(db.String(255), nullable=True)
    sales_rep_source = db.Column(db.String(40), nullable=True)

    intended_to = db.Column(db.String(255), nullable=True)
    intended_cc = db.Column(db.String(255), nullable=True)
    actual_to = db.Column(db.String(255), nullable=True)
    live_mode = db.Column(db.Boolean, nullable=False, default=False)
    email_sent = db.Column(db.Boolean, nullable=False, default=False)
    skip_reason = db.Column(db.String(120), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "bullhorn_company_id",
            name="uq_client_ob_notify_company",
        ),
        Index(
            "ix_client_ob_notify_company_created",
            "bullhorn_company_id",
            "created_at",
        ),
    )
