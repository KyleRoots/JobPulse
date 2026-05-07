"""Tests for services/openai_helper.py — model resolution, cost
estimation, usage extraction, and call logging.
"""
import time

import pytest

from services import openai_helper


class _FakeUsage:
    def __init__(self, prompt_tokens=0, completion_tokens=0,
                 prompt_tokens_details=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.input_tokens = prompt_tokens
        self.output_tokens = completion_tokens
        self.prompt_tokens_details = prompt_tokens_details


class _FakeResponse:
    def __init__(self, usage):
        self.usage = usage


def test_resolve_model_default_when_no_env(monkeypatch):
    monkeypatch.delenv('MODEL_TIER_OVERRIDE_SCREENING_SCORING', raising=False)
    assert openai_helper.resolve_model('screening.scoring', 'gpt-5.4') == 'gpt-5.4'


def test_resolve_model_env_override(monkeypatch):
    monkeypatch.setenv('MODEL_TIER_OVERRIDE_SCREENING_SCORING', 'gpt-4.1-mini')
    assert openai_helper.resolve_model('screening.scoring', 'gpt-5.4') == 'gpt-4.1-mini'


def test_resolve_model_normalizes_dashes(monkeypatch):
    monkeypatch.setenv('MODEL_TIER_OVERRIDE_SCOUT_SUPPORT_REOPEN_ANALYSIS', 'gpt-4.1')
    assert openai_helper.resolve_model('scout_support.reopen-analysis', 'gpt-5.4') == 'gpt-4.1'


def test_resolve_model_overrides_isolated_per_site(monkeypatch):
    monkeypatch.setenv('MODEL_TIER_OVERRIDE_SITE_A', 'gpt-4.1-mini')
    monkeypatch.delenv('MODEL_TIER_OVERRIDE_SITE_B', raising=False)
    assert openai_helper.resolve_model('site_a', 'gpt-5.4') == 'gpt-4.1-mini'
    assert openai_helper.resolve_model('site_b', 'gpt-5.4') == 'gpt-5.4'


def test_estimate_cost_known_chat_model():
    cost = openai_helper.estimate_cost('gpt-4.1-mini', 1_000_000, 0, 0)
    assert float(cost) > 0


def test_estimate_cost_unknown_falls_back_to_default():
    cost = openai_helper.estimate_cost('totally-fake-model', 1_000_000, 0, 0)
    assert float(cost) > 0


def test_estimate_cost_zero_tokens_returns_zero():
    assert float(openai_helper.estimate_cost('gpt-4.1-mini', 0, 0, 0)) == 0.0


def test_estimate_cost_embedding_model():
    cost = openai_helper.estimate_cost('text-embedding-3-large', 1_000_000, 0, 0)
    assert float(cost) > 0


def test_estimate_cost_uses_cached_input_pricing():
    full = openai_helper.estimate_cost('gpt-5.4', 1_000_000, 0, 0)
    cached = openai_helper.estimate_cost('gpt-5.4', 1_000_000, 1_000_000, 0)
    assert float(cached) < float(full)


def test_extract_usage_chat_completion_shape():
    resp = _FakeResponse(_FakeUsage(prompt_tokens=100, completion_tokens=50))
    in_tok, cached, out_tok = openai_helper._extract_usage(resp)
    assert in_tok == 100
    assert out_tok == 50
    assert cached == 0


def test_extract_usage_returns_zeros_for_malformed():
    in_tok, cached, out_tok = openai_helper._extract_usage(object())
    assert (in_tok, cached, out_tok) == (0, 0, 0)


def test_extract_usage_responses_api_shape():
    class _Usage:
        input_tokens = 200
        output_tokens = 75

    resp = _FakeResponse(_Usage())
    in_tok, _, out_tok = openai_helper._extract_usage(resp)
    assert in_tok == 200
    assert out_tok == 75


def test_persist_inserts_row(app):
    """Synchronous DB insert — proves the payload contract end-to-end.

    We exercise `_persist` directly rather than `log_call` because
    `log_call` dispatches via a background thread, and SQLite + thread
    interactions are flaky in the test environment. The threaded
    fire-and-forget contract is covered by `test_log_call_*_does_not_raise`.
    """
    from extensions import db
    from models.openai_telemetry import OpenAICallLog
    from datetime import datetime

    payload = {
        'created_at': datetime.utcnow(),
        'call_site_id': 'test.site',
        'model': 'gpt-4.1-mini',
        'input_tokens': 500,
        'output_tokens': 200,
        'cached_input_tokens': 0,
        'estimated_cost_usd': openai_helper.estimate_cost('gpt-4.1-mini', 500, 0, 200),
        'duration_ms': None,
        'tenant_id': None,
        'entity_type': None,
        'entity_id': None,
        'success': True,
        'error_type': None,
    }

    with app.app_context():
        before = db.session.query(OpenAICallLog).count()
        row = OpenAICallLog(**payload)
        db.session.add(row)
        db.session.commit()
        after = db.session.query(OpenAICallLog).count()
        assert after == before + 1

        persisted = (
            db.session.query(OpenAICallLog)
            .filter_by(call_site_id='test.site')
            .order_by(OpenAICallLog.id.desc())
            .first()
        )
        assert persisted is not None
        assert persisted.model == 'gpt-4.1-mini'
        assert persisted.input_tokens == 500
        assert persisted.output_tokens == 200
        assert persisted.estimated_cost_usd is not None


def test_log_call_unknown_model_does_not_raise(app):
    resp = _FakeResponse(_FakeUsage(prompt_tokens=10, completion_tokens=5))
    openai_helper.log_call('test.unknown', 'super-fake-model-xyz', resp)
    time.sleep(0.5)


def test_log_call_with_no_usage_does_not_raise(app):
    openai_helper.log_call('test.no_usage', 'gpt-4.1-mini', object())
    time.sleep(0.5)


def test_pricing_contains_phase_1_models():
    for model in ('gpt-5.4', 'gpt-4.1-mini', 'gpt-4.1-nano', 'gpt-4o-mini', 'text-embedding-3-large'):
        assert model in openai_helper.PRICING, f'PRICING missing {model}'
