"""Per-environment Sales Rep display-name sync configuration.

Covers:
  1. BullhornEnvironment resolvers — the default (Myticas) environment stays
     enabled with the historical customText3/customText6 mapping; new tenants
     are OFF until configured (the hard invariant + the data-safety gate).
  2. run_salesrep_sync field parameterization — uses per-env field names when
     supplied, falls back to the customText3 → customText6 defaults otherwise,
     and only writes when the display value actually changed.
"""
import pytest

import salesrep_sync_service as svc
from models import BullhornEnvironment


# ── 1. Environment resolvers ───────────────────────────────────────────────
class TestSalesRepEnvResolvers:
    def test_default_env_enabled_when_unset(self):
        env = BullhornEnvironment(
            key="myticas", display_name="Myticas", is_default=True
        )
        assert env.salesrep_sync_active() is True

    def test_new_tenant_off_when_unset(self):
        env = BullhornEnvironment(
            key="qualified_staffing", display_name="QS", is_default=False
        )
        assert env.salesrep_sync_active() is False

    def test_explicit_enable_on_new_tenant(self):
        env = BullhornEnvironment(
            key="qualified_staffing", display_name="QS", is_default=False,
            salesrep_sync_enabled=True,
        )
        assert env.salesrep_sync_active() is True

    def test_explicit_disable_on_default(self):
        env = BullhornEnvironment(
            key="myticas", display_name="Myticas", is_default=True,
            salesrep_sync_enabled=False,
        )
        assert env.salesrep_sync_active() is False

    def test_fields_none_when_unset(self):
        env = BullhornEnvironment(
            key="myticas", display_name="Myticas", is_default=True
        )
        assert env.get_salesrep_source_field() is None
        assert env.get_salesrep_display_field() is None

    def test_fields_returned_and_stripped_when_set(self):
        env = BullhornEnvironment(
            key="qualified_staffing", display_name="QS", is_default=False,
            salesrep_source_field="  customText10 ",
            salesrep_display_field="customText11",
        )
        assert env.get_salesrep_source_field() == "customText10"
        assert env.get_salesrep_display_field() == "customText11"

    def test_blank_fields_resolve_to_none(self):
        env = BullhornEnvironment(
            key="qualified_staffing", display_name="QS", is_default=False,
            salesrep_source_field="   ",
            salesrep_display_field="",
        )
        assert env.get_salesrep_source_field() is None
        assert env.get_salesrep_display_field() is None


# ── 2. Service field parameterization ──────────────────────────────────────
class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeBullhorn:
    """Minimal stand-in: already authenticated so authenticate() is skipped."""

    def __init__(self):
        self.rest_token = "tok"
        self.base_url = "https://rest.example.com/"

    def _get_headers(self):
        return {"BhRestToken": "tok"}

    def authenticate(self):  # pragma: no cover - not expected to be called
        raise AssertionError("authenticate() should not be called when tokened")


def _fake_requests(captured, company):
    """Fake `requests` module: serves one company page, the CorporateUser
    lookup, and captures the write-back POST."""

    class FakeRequests:
        @staticmethod
        def get(url, headers=None, params=None, timeout=None):
            if "query/ClientCorporation" in url:
                captured["query_params"] = params
                if captured.get("queried"):
                    return _Resp({"data": []})
                captured["queried"] = True
                return _Resp({"data": [company]})
            if "entity/CorporateUser/" in url:
                return _Resp({"data": {"firstName": "Jane", "lastName": "Doe"}})
            return _Resp({"data": {}})

        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            captured["post_url"] = url
            captured["post_json"] = json
            return _Resp({"data": {}})

    return FakeRequests


class TestSalesRepFieldParameterization:
    def test_uses_custom_fields(self, monkeypatch):
        captured = {}
        company = {
            "id": 5, "name": "Acme",
            "customText10": "777", "customText11": "Old Rep",
        }
        monkeypatch.setattr(svc, "requests", _fake_requests(captured, company))

        result = svc.run_salesrep_sync(
            _FakeBullhorn(),
            source_field="customText10",
            display_field="customText11",
        )

        assert result["success"] is True
        assert result["updated"] == 1
        assert "customText10" in captured["query_params"]["where"]
        assert "customText10" in captured["query_params"]["fields"]
        assert "customText11" in captured["query_params"]["fields"]
        assert captured["post_json"] == {"customText11": "Jane Doe"}

    def test_defaults_to_standard_fields(self, monkeypatch):
        captured = {}
        company = {
            "id": 5, "name": "Acme",
            "customText3": "777", "customText6": "Old Rep",
        }
        monkeypatch.setattr(svc, "requests", _fake_requests(captured, company))

        result = svc.run_salesrep_sync(_FakeBullhorn())

        assert result["updated"] == 1
        assert "customText3" in captured["query_params"]["fields"]
        assert captured["post_json"] == {"customText6": "Jane Doe"}

    def test_blank_field_args_fall_back_to_defaults(self, monkeypatch):
        captured = {}
        company = {
            "id": 5, "name": "Acme",
            "customText3": "777", "customText6": "Old Rep",
        }
        monkeypatch.setattr(svc, "requests", _fake_requests(captured, company))

        result = svc.run_salesrep_sync(
            _FakeBullhorn(), source_field="  ", display_field=""
        )

        assert result["updated"] == 1
        assert captured["post_json"] == {"customText6": "Jane Doe"}

    def test_no_write_when_display_already_matches(self, monkeypatch):
        captured = {}
        company = {
            "id": 5, "name": "Acme",
            "customText3": "777", "customText6": "Jane Doe",
        }
        monkeypatch.setattr(svc, "requests", _fake_requests(captured, company))

        result = svc.run_salesrep_sync(_FakeBullhorn())

        assert result["updated"] == 0
        assert "post_json" not in captured
