"""Tests for Phase 1 auth hardening (auth_policy + protected settings routes)."""
from __future__ import annotations

import os
import pytest

from auth_policy import (
    MIN_PASSWORD_LENGTH,
    REMEMBER_ME_DAYS,
    SESSION_LIFETIME_HOURS,
    validate_password_strength,
)


class TestPasswordPolicy:
    def test_minimum_length(self):
        ok, msg = validate_password_strength('short')
        assert ok is False
        assert str(MIN_PASSWORD_LENGTH) in msg

    def test_valid_password(self):
        ok, msg = validate_password_strength('a' * MIN_PASSWORD_LENGTH)
        assert ok is True
        assert msg == ''


class TestSessionPolicyConstants:
    def test_shorter_than_legacy_30_day(self):
        assert SESSION_LIFETIME_HOURS <= 24
        assert REMEMBER_ME_DAYS <= 14


class TestSettingsPostRequiresLogin:
    def test_update_settings_post_redirects_unauthenticated(self, client):
        response = client.post('/settings', data={'sftp_hostname': 'evil.example'})
        assert response.status_code == 302
        assert '/login' in response.headers.get('Location', '')

    def test_test_sftp_post_redirects_unauthenticated(self, client):
        response = client.post(
            '/test-sftp-connection',
            json={'sftp_hostname': 'evil.example', 'sftp_username': 'x', 'sftp_password': 'y'},
        )
        assert response.status_code == 302
        assert '/login' in response.headers.get('Location', '')


class TestProductionSessionSecret:
    def test_check_environment_requires_secret_in_production(self, monkeypatch):
        monkeypatch.delenv('SESSION_SECRET', raising=False)
        monkeypatch.setenv('APP_ENV', 'production')
        from importlib import reload
        import main as main_mod
        reload(main_mod)
        with pytest.raises(RuntimeError, match='SESSION_SECRET'):
            main_mod.check_environment()

    def test_check_environment_allows_dev_fallback(self, monkeypatch):
        monkeypatch.delenv('SESSION_SECRET', raising=False)
        monkeypatch.setenv('APP_ENV', 'development')
        from importlib import reload
        import main as main_mod
        reload(main_mod)
        assert main_mod.check_environment() is True
        assert os.environ.get('SESSION_SECRET')
