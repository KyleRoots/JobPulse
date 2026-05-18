"""Worker resilience tests: audit row must ALWAYS be written, even when
Bullhorn returns malformed numeric fields. No PATCH must fire on bad input.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

os.environ.setdefault("SESSION_SECRET", "test-secret")

from app import app, db  # noqa: E402
from models.placement_margin import PlacementMarginCalcLog  # noqa: E402
from placement_margin import worker as worker_mod  # noqa: E402


@pytest.fixture
def flask_ctx():
    with app.app_context():
        db.create_all()
        yield
        db.session.rollback()
        PlacementMarginCalcLog.query.delete()
        db.session.commit()


def _bh_returning(placement_payload):
    """Bullhorn stub that returns the supplied placement payload and
    records whether update_entity was called."""
    bh = MagicMock()
    bh.base_url = "https://example/"
    bh.rest_token = "tok"
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {"data": placement_payload}
    bh.session.get.return_value = response
    bh.update_entity = MagicMock(return_value=True)
    return bh


def test_malformed_bill_rate_writes_audit_row_no_patch(flask_ctx):
    bh = _bh_returning({
        "id": 9999,
        "clientBillRate": "N/A",
        "payRate": 50,
        "customBillRate1": 10,
        "customBillRate2": None,
        "customFloat1": None,
    })
    row = worker_mod.process_placement(bh, 9999, trigger="test")
    assert row.id is not None, "Audit row must be persisted even for bad input"
    # 'N/A' bill rate → None after safe coercion → calculator returns
    # MISSING_BILL_RATE. Either way the audit row exists and no PATCH fires.
    assert row.calc_status in ("invalid_input", "missing_bill_rate")
    assert row.write_success is None
    assert row.computed_margin_pct is None
    assert row.client_bill_rate is None, "Unparseable input must coerce to None"
    bh.update_entity.assert_not_called()


def test_blank_string_inputs_safe(flask_ctx):
    bh = _bh_returning({
        "id": 9998,
        "clientBillRate": "",
        "payRate": "",
        "customBillRate1": "",
        "customBillRate2": "",
        "customFloat1": None,
    })
    row = worker_mod.process_placement(bh, 9998, trigger="test")
    assert row.id is not None
    assert row.calc_status in ("invalid_input", "missing_bill_rate")
    bh.update_entity.assert_not_called()


def test_happy_path_patches_and_writes_row(flask_ctx):
    bh = _bh_returning({
        "id": 9997,
        "clientBillRate": 150,
        "payRate": 125,
        "customBillRate1": 0,
        "customBillRate2": 3,
        "customFloat1": None,
    })
    row = worker_mod.process_placement(bh, 9997, trigger="test")
    assert row.calc_status == "ok"
    assert row.write_success is True
    assert float(row.computed_margin_pct) == 14.67
    bh.update_entity.assert_called_once()
