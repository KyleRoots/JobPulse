"""Tests for the Module-Based AI Cost Forecaster."""
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from app import app as flask_app
from extensions import db
from models.openai_telemetry import OpenAICallLog
from models.cost_forecast import CostForecastOverride, CostForecastScenario
from services.cost_forecaster import (
    MODULE_DEFINITIONS,
    MODULE_BY_KEY,
    derive_unit_costs,
    project_monthly_cost,
    MIN_SAMPLE_FOR_CONFIDENT,
)


@pytest.fixture
def app():
    flask_app.config['TESTING'] = True
    with flask_app.app_context():
        # Clean slate for each test
        db.session.query(OpenAICallLog).delete()
        db.session.query(CostForecastOverride).delete()
        db.session.query(CostForecastScenario).delete()
        db.session.commit()
        yield flask_app
        db.session.query(OpenAICallLog).delete()
        db.session.query(CostForecastOverride).delete()
        db.session.query(CostForecastScenario).delete()
        db.session.commit()


def _seed_calls(site: str, n: int, cost_each: float):
    now = datetime.utcnow()
    for i in range(n):
        db.session.add(OpenAICallLog(
            created_at=now - timedelta(minutes=i),
            call_site_id=site,
            model='gpt-4.1-mini',
            input_tokens=100,
            output_tokens=50,
            cached_input_tokens=0,
            estimated_cost_usd=Decimal(f'{cost_each:.6f}'),
        ))
    db.session.commit()


# ---------------------------------------------------------------- module config

def test_module_definitions_have_unique_keys():
    keys = [m.key for m in MODULE_DEFINITIONS]
    assert len(keys) == len(set(keys))


def test_every_module_has_primary_site_in_its_sites():
    for m in MODULE_DEFINITIONS:
        assert m.primary_site in m.sites, (
            f'{m.key}: primary_site {m.primary_site} not in sites tuple'
        )


def test_module_by_key_lookup():
    assert MODULE_BY_KEY['inbound'].label == 'Inbound'
    assert MODULE_BY_KEY['screening'].primary_site == 'screening.scoring'


# ---------------------------------------------------------------- derive_unit_costs

def test_derive_returns_insufficient_data_when_table_empty(app):
    out = derive_unit_costs(window_days=7)
    for m in MODULE_DEFINITIONS:
        assert out[m.key]['confidence'] == 'insufficient_data'
        assert out[m.key]['unit_cost_usd'] == 0.0
        assert out[m.key]['sample_size'] == 0


def test_derive_low_confidence_below_threshold(app):
    _seed_calls('email_inbound.resume_parse', 5, 0.01)
    out = derive_unit_costs(window_days=7)
    assert out['inbound']['sample_size'] == 5
    assert out['inbound']['confidence'] == 'low'
    assert out['inbound']['unit_cost_usd'] == pytest.approx(0.01, rel=1e-3)


def test_derive_confident_at_or_above_threshold(app):
    _seed_calls('email_inbound.resume_parse', MIN_SAMPLE_FOR_CONFIDENT, 0.02)
    out = derive_unit_costs(window_days=7)
    assert out['inbound']['confidence'] == 'confident'


def test_derive_aggregates_secondary_sites_into_module_cost(app):
    # 10 primary + 10 secondary calls — unit cost should reflect both.
    _seed_calls('email_inbound.resume_parse', 10, 0.01)
    _seed_calls('email_inbound.dedup_validate', 10, 0.005)
    out = derive_unit_costs(window_days=7)
    # total cost = 10*0.01 + 10*0.005 = 0.15; primary count = 10; unit = 0.015
    assert out['inbound']['unit_cost_usd'] == pytest.approx(0.015, rel=1e-3)
    assert out['inbound']['sample_size'] == 10


def test_derive_window_filter_excludes_old_rows(app):
    old = datetime.utcnow() - timedelta(days=30)
    db.session.add(OpenAICallLog(
        created_at=old,
        call_site_id='email_inbound.resume_parse',
        model='gpt-4.1-mini',
        input_tokens=100, output_tokens=50, cached_input_tokens=0,
        estimated_cost_usd=Decimal('0.50'),
    ))
    db.session.commit()
    out = derive_unit_costs(window_days=7)
    assert out['inbound']['sample_size'] == 0


def test_derive_unknown_site_does_not_crash(app):
    _seed_calls('not_a_real_site', 5, 1.00)
    out = derive_unit_costs(window_days=7)
    # All modules still empty; unknown site is silently ignored.
    assert all(out[m.key]['sample_size'] == 0 for m in MODULE_DEFINITIONS)


# ---------------------------------------------------------------- project_monthly_cost

def test_project_single_module_simple_math():
    unit_costs = {'inbound': {'unit_cost_usd': 0.04, 'sample_size': 100,
                              'confidence': 'confident'}}
    out = project_monthly_cost({'inbound': 1000}, unit_costs)
    assert out['monthly_cost_usd'] == 40.0
    assert out['annual_cost_usd'] == 480.0
    assert len(out['breakdown']) == 1
    assert out['breakdown'][0]['source'] == 'confident'


def test_project_multi_module_sum():
    unit_costs = {
        'inbound':   {'unit_cost_usd': 0.04, 'sample_size': 100, 'confidence': 'confident'},
        'screening': {'unit_cost_usd': 0.10, 'sample_size': 50,  'confidence': 'confident'},
    }
    out = project_monthly_cost({'inbound': 1000, 'screening': 500}, unit_costs)
    # 1000*0.04 + 500*0.10 = 40 + 50 = 90
    assert out['monthly_cost_usd'] == 90.0


def test_project_override_supersedes_derived():
    unit_costs = {'screening': {'unit_cost_usd': 0.10, 'sample_size': 100,
                                'confidence': 'confident'}}
    out = project_monthly_cost({'screening': 500}, unit_costs, overrides={'screening': 0.20})
    assert out['monthly_cost_usd'] == 100.0
    assert out['breakdown'][0]['source'] == 'override'


def test_project_zero_volume_excluded():
    unit_costs = {'inbound': {'unit_cost_usd': 0.04, 'sample_size': 100,
                              'confidence': 'confident'}}
    out = project_monthly_cost({'inbound': 0}, unit_costs)
    assert out['monthly_cost_usd'] == 0.0
    assert out['breakdown'] == []


def test_project_unknown_module_skipped():
    out = project_monthly_cost({'made_up_module': 1000}, {})
    assert out['monthly_cost_usd'] == 0.0
    assert out['breakdown'] == []


def test_project_insufficient_data_yields_zero_unless_override():
    unit_costs = {'screening': {'unit_cost_usd': 0.0, 'sample_size': 0,
                                'confidence': 'insufficient_data'}}
    out = project_monthly_cost({'screening': 1000}, unit_costs)
    assert out['monthly_cost_usd'] == 0.0
    assert out['breakdown'][0]['source'] == 'insufficient_data'

    # Same call with an override — now produces a number.
    out2 = project_monthly_cost({'screening': 1000}, unit_costs, overrides={'screening': 0.15})
    assert out2['monthly_cost_usd'] == 150.0


def test_project_negative_override_ignored():
    unit_costs = {'inbound': {'unit_cost_usd': 0.04, 'sample_size': 100,
                              'confidence': 'confident'}}
    out = project_monthly_cost({'inbound': 1000}, unit_costs, overrides={'inbound': -5.0})
    # negative override rejected → falls back to derived
    assert out['monthly_cost_usd'] == 40.0


# ---------------------------------------------------------------- route smoke tests

def test_forecast_route_requires_admin(app):
    client = app.test_client()
    resp = client.get('/admin/ai-cost/forecast')
    # not logged in → redirects to login (302) or 401/403
    assert resp.status_code in (302, 401, 403)
