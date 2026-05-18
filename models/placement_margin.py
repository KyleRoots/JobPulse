"""Placement Net Margin % calculation audit log.

One row per attempt to compute + write the Net Margin % field on a
Bullhorn Placement. Powers the admin UI's "last 50 events" view and
gives Finance a forensic trail when a number ever looks wrong.

This table lives outside the Bullhorn webhook event stream by design —
events come and go (Bullhorn purges them after fetch), but our calc
history is permanent and queryable.
"""
from datetime import datetime
from sqlalchemy import BigInteger, Index, Integer

from extensions import db


class PlacementMarginCalcLog(db.Model):
    """One row per Net Margin % calculation attempt against a Placement."""

    __tablename__ = 'placement_margin_calc_log'

    id = db.Column(
        BigInteger().with_variant(Integer, 'sqlite'),
        primary_key=True, autoincrement=True,
    )
    created_at = db.Column(
        db.DateTime, default=datetime.utcnow, nullable=False, index=True,
    )

    # Bullhorn Placement record this calc was for.
    bullhorn_placement_id = db.Column(db.Integer, nullable=False, index=True)

    # What kicked off this calc: 'webhook', 'backfill', 'manual'.
    trigger = db.Column(db.String(20), nullable=False, default='webhook')

    # Input snapshot — all hourly $ amounts as pulled from Bullhorn at calc time.
    client_bill_rate = db.Column(db.Numeric(12, 4), nullable=True)
    pay_rate = db.Column(db.Numeric(12, 4), nullable=True)
    custom_bill_rate_1 = db.Column(db.Numeric(12, 4), nullable=True)  # Hourly Burden
    custom_bill_rate_2 = db.Column(db.Numeric(12, 4), nullable=True)  # Other Costs Hourly

    # Computed Net Margin %, e.g. 28.5 → represents 28.5%. NULL when status != 'ok'.
    computed_margin_pct = db.Column(db.Numeric(8, 2), nullable=True)

    # Outcome category — one of placement_margin.calculator.MarginStatus values.
    calc_status = db.Column(db.String(40), nullable=False)

    # Did the subsequent PATCH back to Bullhorn succeed? NULL when no PATCH was attempted
    # (e.g. status != 'ok', or value matched existing — see write_skipped_reason).
    write_success = db.Column(db.Boolean, nullable=True)
    write_skipped_reason = db.Column(db.String(80), nullable=True)
    bullhorn_error = db.Column(db.Text, nullable=True)

    # Bullhorn event id that triggered this (when trigger='webhook'), for tracing.
    bullhorn_event_id = db.Column(db.String(64), nullable=True)

    __table_args__ = (
        Index(
            'ix_placement_margin_calc_log_placement_created',
            'bullhorn_placement_id', 'created_at',
        ),
        Index(
            'ix_placement_margin_calc_log_status_created',
            'calc_status', 'created_at',
        ),
    )
