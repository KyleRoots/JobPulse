"""Forecaster persistence — manual unit-cost overrides + saved scenarios.

Two tables:
  cost_forecast_override — one row per module: a super-admin-set unit
    cost (USD per primary-site call) that supersedes the derived value
    on the forecaster page. Used when a module isn't running enough to
    produce a confident derived number (e.g. Screening during a
    cost-recuperation window).

  cost_forecast_scenario — named scenario snapshots (active modules +
    monthly volumes) so admins can recall "Customer A profile",
    "Internal current pace", "Enterprise tier" without retyping.
"""
from datetime import datetime

from sqlalchemy import Index, JSON

from extensions import db


class CostForecastOverride(db.Model):
    __tablename__ = 'cost_forecast_override'

    module_key = db.Column(db.String(40), primary_key=True)
    unit_cost_usd = db.Column(db.Numeric(12, 6), nullable=False, default=0)
    note = db.Column(db.String(200), nullable=True)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow,
                           onupdate=datetime.utcnow, nullable=False)
    updated_by = db.Column(db.String(80), nullable=True)


class CostForecastScenario(db.Model):
    __tablename__ = 'cost_forecast_scenario'

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(120), nullable=False, unique=True)
    description = db.Column(db.String(400), nullable=True)
    # Stored as {"module_key": monthly_volume_int, ...}
    payload = db.Column(JSON, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    created_by = db.Column(db.String(80), nullable=True)

    __table_args__ = (
        Index('ix_cost_forecast_scenario_created', 'created_at'),
    )
