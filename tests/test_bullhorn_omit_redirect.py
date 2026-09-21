"""Qualified / portal-style Bullhorn OAuth omits redirect_uri."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bullhorn_service.auth import AuthMixin, _omit_oauth_redirect_uri


class _AuthHarness(AuthMixin):
    """Minimal stand-in for BullhornService auth tests."""

    BULLHORN_ONE_AUTH_URL = "https://auth-east.example/oauth/authorize"
    BULLHORN_ONE_TOKEN_URL = "https://auth-east.example/oauth/token"
    BULLHORN_ONE_REST_LOGIN_URL = "https://rest-east.example/rest-services/login"
    BULLHORN_ONE_REST_URL = "https://rest45.example/rest-services/fallback/"

    def __init__(self):
        self.client_id = "client-id"
        self.client_secret = "client-secret"
        self.username = "api.user"
        self.password = "secret"
        self.use_bullhorn_one = True
        self.session = MagicMock()
        self.rest_token = None
        self.base_url = None
        self.access_token = None
        self.user_id = None
        self._auth_in_progress = False
        self._last_auth_attempt = None

    def _safe_json_parse(self, response):
        return response.json()


def test_omit_redirect_true_on_qualified_tenant(monkeypatch):
    monkeypatch.delenv("BULLHORN_OMIT_REDIRECT_URI", raising=False)
    monkeypatch.setenv("SCOUT_TENANT", "qualified_staffing")
    assert _omit_oauth_redirect_uri() is True


def test_omit_redirect_false_on_default_tenant(monkeypatch):
    monkeypatch.delenv("BULLHORN_OMIT_REDIRECT_URI", raising=False)
    monkeypatch.delenv("SCOUT_TENANT", raising=False)
    assert _omit_oauth_redirect_uri() is False


def test_omit_redirect_env_override(monkeypatch):
    monkeypatch.setenv("SCOUT_TENANT", "qualified_staffing")
    monkeypatch.setenv("BULLHORN_OMIT_REDIRECT_URI", "false")
    assert _omit_oauth_redirect_uri() is False
    monkeypatch.delenv("SCOUT_TENANT", raising=False)
    monkeypatch.setenv("BULLHORN_OMIT_REDIRECT_URI", "true")
    assert _omit_oauth_redirect_uri() is True


def _ok_json(payload):
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = payload
    resp.text = ""
    return resp


def test_direct_login_omits_redirect_uri_for_qualified(monkeypatch):
    monkeypatch.setenv("SCOUT_TENANT", "qualified_staffing")
    bh = _AuthHarness()

    auth_resp = MagicMock()
    auth_resp.status_code = 302
    auth_resp.headers = {
        "Location": "https://example.invalid/callback?code=AUTHCODE1"
    }

    token_resp = _ok_json({"access_token": "atok"})
    rest_resp = _ok_json(
        {
            "BhRestToken": "resttok",
            "restUrl": "https://rest45.example/rest-services/clp2rd/",
            "userId": 99,
        }
    )

    bh.session.get.side_effect = [auth_resp]
    bh.session.post.side_effect = [token_resp, rest_resp]

    assert bh._direct_login() is True
    assert bh.base_url == "https://rest45.example/rest-services/clp2rd/"
    assert bh.rest_token == "resttok"

    auth_call = bh.session.get.call_args
    auth_params = auth_call.kwargs.get("params") or auth_call[1].get("params")
    assert "redirect_uri" not in auth_params

    token_call = bh.session.post.call_args_list[0]
    token_data = token_call.kwargs.get("data") or token_call[1].get("data")
    assert "redirect_uri" not in token_data


def test_direct_login_sends_redirect_uri_for_myticas(monkeypatch):
    monkeypatch.delenv("SCOUT_TENANT", raising=False)
    monkeypatch.delenv("BULLHORN_OMIT_REDIRECT_URI", raising=False)
    monkeypatch.setenv("OAUTH_REDIRECT_BASE_URL", "https://app.scoutgenius.ai")
    bh = _AuthHarness()

    auth_resp = MagicMock()
    auth_resp.status_code = 302
    auth_resp.headers = {
        "Location": "https://app.scoutgenius.ai/bullhorn/oauth/callback?code=AUTHCODE2"
    }
    token_resp = _ok_json({"access_token": "atok"})
    rest_resp = _ok_json(
        {
            "BhRestToken": "resttok",
            "restUrl": "https://rest45.example/rest-services/dcc900/",
            "userId": 1,
        }
    )
    bh.session.get.side_effect = [auth_resp]
    bh.session.post.side_effect = [token_resp, rest_resp]

    assert bh._direct_login() is True

    auth_params = bh.session.get.call_args.kwargs["params"]
    assert (
        auth_params["redirect_uri"]
        == "https://app.scoutgenius.ai/bullhorn/oauth/callback"
    )
    token_data = bh.session.post.call_args_list[0].kwargs["data"]
    assert (
        token_data["redirect_uri"]
        == "https://app.scoutgenius.ai/bullhorn/oauth/callback"
    )
