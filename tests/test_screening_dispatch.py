"""
Tests for utils/screening_dispatch.py — the M3 manual-trigger handoff helper.

Covers the four behaviour paths and the contract callers depend on:
  1. Periodic vetting job exists → modify_job advances it; returns
     enqueued=True with mode='advanced'.
  2. Periodic job missing → fall back to add_job one-shot; returns
     enqueued=True with mode='one_shot'.
  3. Duplicate one-shot click → ConflictingIdError treated as success
     (already_queued); returns enqueued=True.
  4. Scheduler not running → returns enqueued=False with reason_code
     scheduler_down (cycle still picked up on next tick when scheduler
     resumes).
  5. Unexpected scheduler import failure → returns enqueued=False with
     reason_code error (helper never raises).
  6. Unexpected modify_job failure falls through to one-shot path
     (defensive — covers transient APScheduler bugs without dropping
     the user's click).

These tests intentionally do NOT exercise live APScheduler; they patch
`app.scheduler` at import time and assert the helper's contract.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest


@pytest.fixture
def fake_scheduler(monkeypatch):
    """Install a MagicMock as `app.scheduler` for the duration of the test."""
    fake = MagicMock()
    fake.running = True
    # Default: periodic job exists
    fake.get_job.return_value = MagicMock(id='candidate_vetting_cycle')

    fake_app_module = types.ModuleType('app')
    fake_app_module.scheduler = fake
    monkeypatch.setitem(sys.modules, 'app', fake_app_module)

    # Provide a stub tasks module so the one-shot fallback path can import it
    fake_tasks = types.ModuleType('tasks')
    fake_tasks.run_candidate_vetting_cycle = lambda: None
    monkeypatch.setitem(sys.modules, 'tasks', fake_tasks)

    return fake


def _import_helper():
    """Re-import the helper fresh so each test sees the current sys.modules."""
    if 'utils.screening_dispatch' in sys.modules:
        del sys.modules['utils.screening_dispatch']
    from utils.screening_dispatch import enqueue_vetting_now
    return enqueue_vetting_now


def test_advances_existing_periodic_job(fake_scheduler):
    """Path 1: periodic job exists → modify_job is called with next_run_time=now."""
    enqueue_vetting_now = _import_helper()

    result = enqueue_vetting_now(reason='unit_test')

    assert result['enqueued'] is True
    assert result['mode'] == 'advanced'
    assert result['reason_code'] == 'job_advanced'
    fake_scheduler.modify_job.assert_called_once()
    call_args = fake_scheduler.modify_job.call_args
    assert call_args[0][0] == 'candidate_vetting_cycle'
    assert 'next_run_time' in call_args[1]


def test_falls_back_to_one_shot_when_periodic_job_missing(fake_scheduler):
    """Path 2: periodic job not registered → add_job one-shot is used."""
    fake_scheduler.get_job.return_value = None

    enqueue_vetting_now = _import_helper()
    result = enqueue_vetting_now(reason='unit_test')

    assert result['enqueued'] is True
    assert result['mode'] == 'one_shot'
    assert result['reason_code'] == 'one_shot_added'
    fake_scheduler.add_job.assert_called_once()
    kwargs = fake_scheduler.add_job.call_args[1]
    assert kwargs['id'] == 'candidate_vetting_cycle_oneshot'
    assert kwargs['trigger'] == 'date'
    assert kwargs['replace_existing'] is False


def test_duplicate_one_shot_click_is_idempotent(fake_scheduler):
    """Path 3: ConflictingIdError on duplicate add_job → already_queued success."""
    fake_scheduler.get_job.return_value = None

    class FakeConflictingIdError(Exception):
        pass
    FakeConflictingIdError.__name__ = 'ConflictingIdError'
    fake_scheduler.add_job.side_effect = FakeConflictingIdError(
        "Job identifier candidate_vetting_cycle_oneshot already in use"
    )

    enqueue_vetting_now = _import_helper()
    result = enqueue_vetting_now(reason='dup_click')

    assert result['enqueued'] is True
    assert result['mode'] == 'one_shot'
    assert result['reason_code'] == 'already_queued'


def test_scheduler_not_running_returns_noop(fake_scheduler):
    """Path 4: scheduler.running is False → enqueued=False, reason_code scheduler_down."""
    fake_scheduler.running = False

    enqueue_vetting_now = _import_helper()
    result = enqueue_vetting_now(reason='down_test')

    assert result['enqueued'] is False
    assert result['mode'] == 'noop'
    assert result['reason_code'] == 'scheduler_down'
    fake_scheduler.modify_job.assert_not_called()
    fake_scheduler.add_job.assert_not_called()


def test_scheduler_import_failure_returns_error(monkeypatch):
    """Path 5: importing app.scheduler raises → returns enqueued=False, error code."""
    fake_app_module = types.ModuleType('app')
    # Property access raises when attribute is read
    def _raise(*_a, **_k):
        raise RuntimeError("boom")
    monkeypatch.setitem(sys.modules, 'app', fake_app_module)
    # No `scheduler` attr at all → AttributeError on `from app import scheduler`

    if 'utils.screening_dispatch' in sys.modules:
        del sys.modules['utils.screening_dispatch']
    from utils.screening_dispatch import enqueue_vetting_now

    result = enqueue_vetting_now(reason='import_fail')

    assert result['enqueued'] is False
    assert result['mode'] == 'error'
    assert result['reason_code'] == 'error'


def test_modify_job_failure_falls_through_to_one_shot(fake_scheduler):
    """Path 6: modify_job raises unexpectedly → fall through to one-shot path."""
    # Periodic job exists, but modify_job blows up.
    fake_scheduler.modify_job.side_effect = RuntimeError("transient apscheduler bug")

    enqueue_vetting_now = _import_helper()
    result = enqueue_vetting_now(reason='transient')

    assert result['enqueued'] is True
    assert result['mode'] == 'one_shot'
    fake_scheduler.add_job.assert_called_once()


def test_helper_never_raises_on_unexpected_add_job_failure(fake_scheduler):
    """Defensive: any unexpected add_job exception → enqueued=False, error code."""
    fake_scheduler.get_job.return_value = None
    fake_scheduler.add_job.side_effect = RuntimeError("something else broke")

    enqueue_vetting_now = _import_helper()
    result = enqueue_vetting_now(reason='unknown_fail')

    assert result['enqueued'] is False
    assert result['mode'] == 'error'
    assert result['reason_code'] == 'error'


def test_returns_dict_with_full_contract(fake_scheduler):
    """All four contract keys must be present on every return path."""
    enqueue_vetting_now = _import_helper()
    result = enqueue_vetting_now(reason='contract_check')
    assert set(result.keys()) == {'enqueued', 'mode', 'reason', 'reason_code'}
    assert isinstance(result['enqueued'], bool)
    assert isinstance(result['mode'], str)
    assert isinstance(result['reason'], str)
    assert isinstance(result['reason_code'], str)
