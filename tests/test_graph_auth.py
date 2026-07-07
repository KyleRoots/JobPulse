"""Tests for Microsoft Graph auth mode resolution (Railway Entra vs Replit)."""

import pytest

from utils import graph_auth


@pytest.fixture(autouse=True)
def _clear_token_cache():
    graph_auth.invalidate_graph_token_cache()
    yield
    graph_auth.invalidate_graph_token_cache()


@pytest.fixture
def clean_graph_env(monkeypatch):
    for key in (
        "GRAPH_AUTH_MODE",
        "GRAPH_MAILBOX_UPN",
        "MICROSOFT_CLIENT_ID",
        "MICROSOFT_CLIENT_SECRET",
        "MICROSOFT_TENANT_ID",
        "REPLIT_CONNECTORS_HOSTNAME",
        "REPL_IDENTITY",
        "WEB_REPL_RENEWAL",
    ):
        monkeypatch.delenv(key, raising=False)


def test_resolve_mode_explicit_entra(clean_graph_env, monkeypatch):
    monkeypatch.setenv("GRAPH_AUTH_MODE", "entra")
    assert graph_auth.resolve_graph_auth_mode() == "entra"


def test_resolve_mode_auto_prefers_replit_when_connector_present(clean_graph_env, monkeypatch):
    monkeypatch.setenv("GRAPH_AUTH_MODE", "auto")
    monkeypatch.setenv("REPLIT_CONNECTORS_HOSTNAME", "connectors.replit.com")
    monkeypatch.setenv("REPL_IDENTITY", "test-token")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "entra-client")
    assert graph_auth.resolve_graph_auth_mode() == "replit"


def test_resolve_mode_auto_entra_when_no_replit(clean_graph_env, monkeypatch):
    monkeypatch.setenv("GRAPH_AUTH_MODE", "auto")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "992c198d-79b1-4640-84d1-b177a0167f26")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "8c6e8479-fc78-4bb8-ab9b-db3fc22fbcf6")
    assert graph_auth.resolve_graph_auth_mode() == "entra"


def test_graph_user_base_path_entra(clean_graph_env, monkeypatch):
    monkeypatch.setenv("GRAPH_MAILBOX_UPN", "Apply@myticas.com")
    assert graph_auth.graph_user_base_path("entra") == "/users/Apply@myticas.com"


def test_graph_user_base_path_replit():
    assert graph_auth.graph_user_base_path("replit") == "/me"


def test_fetch_entra_token(monkeypatch):
    graph_auth.invalidate_graph_token_cache()
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "client-id")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "client-secret")
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "tenant-id")

    class FakeResp:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "test-token", "expires_in": 3600}

        text = ""

    monkeypatch.setattr(graph_auth.requests, "post", lambda *a, **k: FakeResp())
    token = graph_auth.get_graph_access_token()
    assert token == "test-token"


def test_graph_mail_service_uses_users_path_for_entra(clean_graph_env, monkeypatch):
    monkeypatch.setenv("GRAPH_AUTH_MODE", "entra")
    monkeypatch.setenv("GRAPH_MAILBOX_UPN", "apply@myticas.com")
    monkeypatch.setenv("MICROSOFT_CLIENT_ID", "c")
    monkeypatch.setenv("MICROSOFT_CLIENT_SECRET", "s")
    monkeypatch.setenv("MICROSOFT_TENANT_ID", "t")

    from graph_mail_service import GraphMailService

    svc = GraphMailService()
    assert svc._user_base() == "/users/apply@myticas.com"
    assert svc.get_connected_address() == "apply@myticas.com"
