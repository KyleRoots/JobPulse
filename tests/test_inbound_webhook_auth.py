"""
Tests for inbound email webhook authentication (Task #113).

Verifies that /api/email/inbound enforces the shared-secret token gate and
that no processing path is reachable when authentication fails.
"""

import os
import pytest


ENDPOINT = '/api/email/inbound'
GOOD_SECRET = 'test-secret-abc123'


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post(client, secret_param=None, data=None):
    """POST to the inbound webhook; optionally include the webhook_secret param."""
    url = ENDPOINT
    if secret_param is not None:
        url = f'{ENDPOINT}?webhook_secret={secret_param}'
    return client.post(
        url,
        data=data or {'from': 'test@example.com', 'subject': 'Test'},
        content_type='application/x-www-form-urlencoded',
    )


# ---------------------------------------------------------------------------
# GET (health check) — never gated
# ---------------------------------------------------------------------------

class TestGetAlwaysAllowed:
    def test_get_no_secret_env(self, client, monkeypatch):
        """GET is always allowed regardless of env-var configuration."""
        monkeypatch.delenv('SENDGRID_INBOUND_WEBHOOK_SECRET', raising=False)
        monkeypatch.delenv('SENDGRID_INBOUND_PARSE_ENABLED', raising=False)
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['status'] == 'ok'
        assert data.get('parse_enabled') is False

    def test_get_with_secret_env(self, client, monkeypatch):
        """GET is allowed even when the secret IS configured."""
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', GOOD_SECRET)
        monkeypatch.delenv('SENDGRID_INBOUND_PARSE_ENABLED', raising=False)
        resp = client.get(ENDPOINT)
        assert resp.status_code == 200
        assert resp.get_json().get('parse_enabled') is True


# ---------------------------------------------------------------------------
# POST — secret env var not configured → 200 disabled (mailbox-pull primary)
# ---------------------------------------------------------------------------

class TestPostMissingEnvVar:
    def test_no_env_var_no_param_returns_200_disabled(self, client, monkeypatch):
        """Unset secret → 200 acknowledge (stops SendGrid retries); no processing."""
        monkeypatch.delenv('SENDGRID_INBOUND_WEBHOOK_SECRET', raising=False)
        monkeypatch.delenv('SENDGRID_INBOUND_PARSE_ENABLED', raising=False)
        resp = _post(client, secret_param=None)
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data.get('status') == 'disabled'

    def test_no_env_var_with_param_still_disabled(self, client, monkeypatch):
        """Even with a webhook_secret param, disabled when env secret not set."""
        monkeypatch.delenv('SENDGRID_INBOUND_WEBHOOK_SECRET', raising=False)
        resp = _post(client, secret_param='anything')
        assert resp.status_code == 200
        assert resp.get_json().get('status') == 'disabled'

    def test_explicit_disable_flag_returns_200(self, client, monkeypatch):
        """SENDGRID_INBOUND_PARSE_ENABLED=false disables even if a secret exists."""
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', GOOD_SECRET)
        monkeypatch.setenv('SENDGRID_INBOUND_PARSE_ENABLED', 'false')
        resp = _post(client, secret_param=GOOD_SECRET)
        assert resp.status_code == 200
        assert resp.get_json().get('status') == 'disabled'

    def test_processing_not_reached_when_disabled(self, client, monkeypatch):
        """No email processing when the webhook door is closed."""
        monkeypatch.delenv('SENDGRID_INBOUND_WEBHOOK_SECRET', raising=False)
        reached = []

        monkeypatch.setattr(
            'email_inbound_service.EmailInboundService.process_email',
            lambda *a, **kw: reached.append(True) or {'success': True},
        )
        _post(client)
        assert reached == [], "process_email must NOT be called when disabled"


# ---------------------------------------------------------------------------
# POST — secret env var configured, param missing → 403
# ---------------------------------------------------------------------------

class TestPostMissingParam:
    def test_missing_param_returns_403(self, client, monkeypatch):
        """POST without webhook_secret query param → 403 when env var is set."""
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', GOOD_SECRET)
        resp = _post(client, secret_param=None)
        assert resp.status_code == 403

    def test_403_body_is_json_unauthorized(self, client, monkeypatch):
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', GOOD_SECRET)
        resp = _post(client, secret_param=None)
        data = resp.get_json()
        assert data is not None
        assert data['success'] is False

    def test_processing_not_reached_when_param_missing(self, client, monkeypatch):
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', GOOD_SECRET)
        reached = []

        monkeypatch.setattr(
            'email_inbound_service.EmailInboundService.process_email',
            lambda *a, **kw: reached.append(True) or {'success': True},
        )
        _post(client, secret_param=None)
        assert reached == [], "process_email must NOT be called when param absent"


# ---------------------------------------------------------------------------
# POST — secret env var configured, param wrong → 403
# ---------------------------------------------------------------------------

class TestPostWrongSecret:
    def test_wrong_secret_returns_403(self, client, monkeypatch):
        """POST with an incorrect webhook_secret → 403."""
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', GOOD_SECRET)
        resp = _post(client, secret_param='wrong-value')
        assert resp.status_code == 403

    def test_empty_string_param_returns_403(self, client, monkeypatch):
        """An empty webhook_secret query param is treated as missing → 403."""
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', GOOD_SECRET)
        resp = _post(client, secret_param='')
        assert resp.status_code == 403

    def test_processing_not_reached_when_wrong_secret(self, client, monkeypatch):
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', GOOD_SECRET)
        reached = []

        monkeypatch.setattr(
            'email_inbound_service.EmailInboundService.process_email',
            lambda *a, **kw: reached.append(True) or {'success': True},
        )
        _post(client, secret_param='wrong-value')
        assert reached == [], "process_email must NOT be called on wrong secret"


# ---------------------------------------------------------------------------
# POST — correct secret → accepted (202 / 200, no auth rejection)
# ---------------------------------------------------------------------------

class TestPostCorrectSecret:
    def test_correct_secret_passes_auth_gate(self, client, monkeypatch):
        """POST with the correct webhook_secret must NOT return 403 or 503."""
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', GOOD_SECRET)

        # Stub out the background thread so the test stays synchronous and fast
        monkeypatch.setattr('threading.Thread', _NullThread)

        resp = _post(client, secret_param=GOOD_SECRET)
        assert resp.status_code not in (403, 503), (
            f"Expected auth to pass but got {resp.status_code}: {resp.get_json()}"
        )

    def test_correct_secret_body_accepted(self, client, monkeypatch):
        """Correct secret → response indicates the email was accepted."""
        monkeypatch.setenv('SENDGRID_INBOUND_WEBHOOK_SECRET', GOOD_SECRET)
        monkeypatch.setattr('threading.Thread', _NullThread)

        resp = _post(client, secret_param=GOOD_SECRET)
        data = resp.get_json()
        assert data is not None
        assert data.get('success') is True


# ---------------------------------------------------------------------------
# Helper: no-op Thread replacement for synchronous tests
# ---------------------------------------------------------------------------

class _NullThread:
    """Drop-in replacement for threading.Thread that does nothing.

    The webhook handler spawns a daemon thread for background processing.
    In tests we want to verify only the auth layer (not the full pipeline),
    so we swap out Thread with this stub to keep tests fast and isolated.
    """
    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        pass
