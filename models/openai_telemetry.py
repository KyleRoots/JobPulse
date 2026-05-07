"""OpenAI call telemetry — per-invocation usage + estimated cost log.

Single append-only table populated by `services.openai_helper.log_call()`.
Powers the AI Cost dashboard tile and Phase 1 cost-reduction monitoring.
"""
from datetime import datetime
from sqlalchemy import BigInteger, Index, Integer

from extensions import db


class OpenAICallLog(db.Model):
    """One row per OpenAI API call across the platform."""
    __tablename__ = 'openai_call_log'

    id = db.Column(BigInteger().with_variant(Integer, 'sqlite'), primary_key=True, autoincrement=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)

    # Logical site identifier — e.g. 'scout_vetting.questions', 'job_classification'.
    call_site_id = db.Column(db.String(80), nullable=False, index=True)

    # Resolved model actually sent to OpenAI (after MODEL_TIER_OVERRIDE_* applied).
    model = db.Column(db.String(80), nullable=False)

    # Token usage from response.usage. 0 when not reported.
    input_tokens = db.Column(db.Integer, default=0, nullable=False)
    output_tokens = db.Column(db.Integer, default=0, nullable=False)
    cached_input_tokens = db.Column(db.Integer, default=0, nullable=False)

    # Estimated cost in USD using the central PRICING table.
    estimated_cost_usd = db.Column(db.Numeric(12, 6), default=0, nullable=False)

    duration_ms = db.Column(db.Integer, nullable=True)

    # Optional context for attribution (best-effort, may be null).
    tenant_id = db.Column(db.String(80), nullable=True, index=True)
    customer_id = db.Column(db.String(80), nullable=True, index=True)
    entity_type = db.Column(db.String(40), nullable=True)
    entity_id = db.Column(db.String(80), nullable=True)

    success = db.Column(db.Boolean, default=True, nullable=False)
    error_type = db.Column(db.String(120), nullable=True)

    __table_args__ = (
        Index('ix_openai_call_log_site_created', 'call_site_id', 'created_at'),
        Index('ix_openai_call_log_tenant_created', 'tenant_id', 'created_at'),
        Index('ix_openai_call_log_customer_created', 'customer_id', 'created_at'),
    )
