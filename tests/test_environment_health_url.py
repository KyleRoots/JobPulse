"""Production environment monitor URL resolution (ScoutGenius vs stale lyntrix)."""
import os

from tasks.monitoring import (
    PRODUCTION_HEALTH_BASE_URL,
    _resolve_production_health_url,
)


def test_defaults_to_scoutgenius():
    assert _resolve_production_health_url(None) == PRODUCTION_HEALTH_BASE_URL
    assert _resolve_production_health_url('') == PRODUCTION_HEALTH_BASE_URL


def test_migrates_lyntrix_to_scoutgenius(monkeypatch):
    monkeypatch.delenv('ENVIRONMENT_HEALTH_URL', raising=False)
    monkeypatch.delenv('SCOUTGENIUS_PUBLIC_URL', raising=False)
    assert (
        _resolve_production_health_url('https://jobpulse.lyntrix.ai')
        == PRODUCTION_HEALTH_BASE_URL
    )


def test_keeps_non_lyntrix_override():
    assert (
        _resolve_production_health_url('https://jobpulse-production-e1ee.up.railway.app')
        == 'https://jobpulse-production-e1ee.up.railway.app'
    )


def test_env_override_when_lyntrix_current(monkeypatch):
    monkeypatch.setenv('ENVIRONMENT_HEALTH_URL', 'https://custom.example/health-base')
    assert (
        _resolve_production_health_url('https://jobpulse.lyntrix.ai')
        == 'https://custom.example/health-base'
    )


def test_keeps_scoutgenius():
    assert (
        _resolve_production_health_url('https://app.scoutgenius.ai')
        == 'https://app.scoutgenius.ai'
    )
