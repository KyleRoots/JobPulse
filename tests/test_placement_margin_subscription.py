"""Regression tests for placement_margin.subscription.fetch_events.

Specifically guards the empty-body status-ordering fix: an empty body
must ONLY be treated as "idle" for 204 or 200; any non-2xx with an
empty body must still surface as an ERROR-level log (silent failures
in this path were a real risk for the finance-critical PATCH pipeline).
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("SESSION_SECRET", "test-secret")

from placement_margin import subscription as sub_mod  # noqa: E402


def _bh(stub_response):
    bh = MagicMock()
    bh.rest_token = "tok"
    bh.base_url = "https://example/"
    bh.session.get.return_value = stub_response
    return bh


def _resp(status_code: int, text: str = "", json_payload=None):
    r = MagicMock()
    r.status_code = status_code
    r.text = text
    if json_payload is not None:
        r.json.return_value = json_payload
    else:
        r.json.side_effect = ValueError("no body")
    return r


def test_204_returns_empty_no_log(caplog):
    with caplog.at_level("ERROR", logger="placement_margin.subscription"):
        events = sub_mod.fetch_events(_bh(_resp(204)))
    assert events == []
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_200_empty_body_returns_empty_no_log(caplog):
    with caplog.at_level("ERROR", logger="placement_margin.subscription"):
        events = sub_mod.fetch_events(_bh(_resp(200, text="")))
    assert events == []
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


def test_500_empty_body_logs_error(caplog):
    """Critical: non-2xx with empty body must NOT be silently treated as idle."""
    with caplog.at_level("ERROR", logger="placement_margin.subscription"):
        events = sub_mod.fetch_events(_bh(_resp(500, text="")))
    assert events == []
    errs = [r for r in caplog.records if r.levelname == "ERROR"]
    assert errs, "500 with empty body must produce an ERROR log"
    assert "500" in errs[-1].getMessage()


def test_400_entity_not_found_logs_warning(caplog):
    body = '{"errorMessage":"EntityNotFoundException: foo","errorCode":400}'
    with caplog.at_level("WARNING", logger="placement_margin.subscription"):
        events = sub_mod.fetch_events(_bh(_resp(400, text=body)))
    assert events == []
    warns = [r for r in caplog.records if r.levelname == "WARNING"]
    assert warns and "not found" in warns[-1].getMessage().lower()


def test_200_with_events_returns_list():
    payload = {"events": [{"entityId": 1, "entityName": "Placement"}]}
    events = sub_mod.fetch_events(_bh(_resp(200, text='{"events":[1]}', json_payload=payload)))
    assert events == [{"entityId": 1, "entityName": "Placement"}]
