"""Tests for Qualified Job → Candidate Internal Department mirroring."""
import os
from unittest.mock import MagicMock

import pytest

from utils.job_internal_department import (
    CANDIDATE_INTERNAL_DEPARTMENT_FIELD,
    apply_job_internal_department_to_candidate_payload,
    fetch_job_internal_department,
    should_mirror_job_internal_department,
)


def test_should_mirror_only_on_qualified(monkeypatch):
    monkeypatch.delenv('SCOUT_TENANT', raising=False)
    assert should_mirror_job_internal_department() is False
    monkeypatch.setenv('SCOUT_TENANT', 'qualified_staffing')
    assert should_mirror_job_internal_department() is True
    monkeypatch.setenv('SCOUT_TENANT', 'myticas')
    assert should_mirror_job_internal_department() is False


def test_apply_payload_sets_custom_text3():
    payload = {'firstName': 'A'}
    assert apply_job_internal_department_to_candidate_payload(payload, 'Dalton') is True
    assert payload[CANDIDATE_INTERNAL_DEPARTMENT_FIELD] == 'Dalton'


def test_apply_payload_skips_blank():
    payload = {}
    assert apply_job_internal_department_to_candidate_payload(payload, '  ') is False
    assert CANDIDATE_INTERNAL_DEPARTMENT_FIELD not in payload


def test_fetch_job_internal_department_reads_correlated_custom_text1():
    bh = MagicMock()
    bh.base_url = 'https://rest.example/rest-services/x/'
    bh.rest_token = 'tok'
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'data': {'id': 71006, 'correlatedCustomText1': 'Dalton'}}
    bh.session.get.return_value = resp
    bh._safe_json_parse.side_effect = lambda r: r.json()

    assert fetch_job_internal_department(bh, 71006) == 'Dalton'
    args, kwargs = bh.session.get.call_args
    assert 'JobOrder/71006' in args[0]
    assert 'correlatedCustomText1' in kwargs['params']['fields']


def test_fetch_job_internal_department_blank_returns_none():
    bh = MagicMock()
    bh.base_url = 'https://rest.example/rest-services/x/'
    bh.rest_token = 'tok'
    resp = MagicMock()
    resp.status_code = 200
    resp.json.return_value = {'data': {'id': 1, 'correlatedCustomText1': ''}}
    bh.session.get.return_value = resp
    bh._safe_json_parse.side_effect = lambda r: r.json()
    assert fetch_job_internal_department(bh, 1) is None


def test_fetch_job_internal_department_fail_soft():
    assert fetch_job_internal_department(None, 1) is None
    assert fetch_job_internal_department(MagicMock(), None) is None
    bh = MagicMock()
    bh.base_url = 'https://rest.example/'
    bh.rest_token = 'tok'
    bh.session.get.side_effect = RuntimeError('boom')
    assert fetch_job_internal_department(bh, 99) is None
