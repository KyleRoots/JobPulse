"""Tests for the edit-preserving guards across BOTH cleanup paths (May 2026).

Two paths can delete `JobVettingRequirements` rows during a monitoring cycle:
  (1) `incremental_monitoring_service._log_auto_removal_activity` — runs when a
      job is actively auto-removed from a tearsheet during the cycle.
  (2) `screening/job_management.JobManagementMixin.sync_requirements_with_active_jobs`
      — runs every cycle and prunes ANY requirement row whose job is not in the
      current active tearsheet set. This was the primary leak: even rows with
      recruiter edits were silently deleted whenever a job briefly disappeared.

Both paths must now skip rows with non-empty `edited_requirements`.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch
from app import app, db
from models import JobVettingRequirements
from incremental_monitoring_service import IncrementalMonitoringService
from utils.requirements_pruning import ABSENCE_GRACE_HOURS, clear_absence_marks


@pytest.fixture
def app_ctx():
    with app.app_context():
        yield


@pytest.fixture(autouse=True)
def _isolate_requirements(app_ctx):
    """`sync_requirements_with_active_jobs` scans the whole table, so rows left
    behind by a sibling test change its counts. Debounced cleanup keeps rows
    alive across a test, which makes that bleed easy to hit."""
    JobVettingRequirements.query.delete()
    db.session.commit()
    yield
    JobVettingRequirements.query.delete()
    db.session.commit()


def _mk_row(job_id, *, edited=None, ai="AI baseline reqs"):
    row = JobVettingRequirements(
        bullhorn_job_id=job_id,
        job_title=f"Test Job {job_id}",
        ai_interpreted_requirements=ai,
        edited_requirements=edited,
        requirements_edited_by="recruiter@example.com" if edited else None,
        requirements_edited_at=datetime.utcnow() if edited else None,
    )
    db.session.add(row)
    return row


def test_auto_removal_preserves_edited_rows(app_ctx, caplog):
    """Edited rows survive; non-edited rows are deleted."""
    edited_id = 9_999_001
    plain_id = 9_999_002
    other_id = 9_999_003  # in DB but not in removal set — must be untouched

    JobVettingRequirements.query.filter(
        JobVettingRequirements.bullhorn_job_id.in_([edited_id, plain_id, other_id])
    ).delete(synchronize_session=False)
    db.session.commit()

    _mk_row(edited_id, edited="- 5+ yrs Python\n- AWS hands-on")
    _mk_row(plain_id, edited=None)
    _mk_row(other_id, edited=None)
    db.session.commit()

    svc = IncrementalMonitoringService.__new__(IncrementalMonitoringService)
    svc.logger = __import__("logging").getLogger("test_auto_removal_guard")
    svc.auto_removed_jobs = [
        {"job_id": edited_id, "job_title": "Edited Job", "tearsheet_id": 1, "reason": "test"},
        {"job_id": plain_id,  "job_title": "Plain Job",  "tearsheet_id": 1, "reason": "test"},
    ]

    with caplog.at_level("WARNING"):
        svc._log_auto_removal_activity()

    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=edited_id).first() is not None, \
        "Edited row must be preserved across auto-removal cleanup"

    plain_row = JobVettingRequirements.query.filter_by(bullhorn_job_id=plain_id).first()
    assert plain_row is not None, "Non-edited row is debounced, not deleted on first miss"
    assert plain_row.tearsheet_absent_since is not None, "Non-edited row must be stamped"

    other_row = JobVettingRequirements.query.filter_by(bullhorn_job_id=other_id).first()
    assert other_row is not None, "Unrelated row must not be touched"
    assert other_row.tearsheet_absent_since is None, \
        "Unrelated row must not be stamped absent"

    protected_log = [r for r in caplog.records if "auto_removal_edit_protected" in r.message]
    assert any(str(edited_id) in r.message for r in protected_log), \
        "A WARNING log line must identify the protected job_id for ops visibility"

    JobVettingRequirements.query.filter(
        JobVettingRequirements.bullhorn_job_id.in_([edited_id, other_id])
    ).delete(synchronize_session=False)
    db.session.commit()


def test_auto_removal_with_only_edited_rows_deletes_nothing(app_ctx):
    """If every removal candidate is edit-protected, no DELETE runs."""
    job_id = 9_999_010
    JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete(synchronize_session=False)
    db.session.commit()
    _mk_row(job_id, edited="- custom requirement")
    db.session.commit()

    svc = IncrementalMonitoringService.__new__(IncrementalMonitoringService)
    svc.logger = __import__("logging").getLogger("test_auto_removal_guard")
    svc.auto_removed_jobs = [
        {"job_id": job_id, "job_title": "Edited", "tearsheet_id": 1, "reason": "test"},
    ]

    svc._log_auto_removal_activity()

    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first() is not None

    JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete(synchronize_session=False)
    db.session.commit()


def _run_auto_removal(job_id, title="Plain"):
    svc = IncrementalMonitoringService.__new__(IncrementalMonitoringService)
    svc.logger = __import__("logging").getLogger("test_auto_removal_guard")
    svc.auto_removed_jobs = [
        {"job_id": job_id, "job_title": title, "tearsheet_id": 1, "reason": "test"},
    ]
    svc._log_auto_removal_activity()


def test_auto_removal_first_miss_only_marks(app_ctx):
    """Debounce: one absence does not delete. Deleting on the first miss is
    what let this path and requirements maintenance churn a job every 5
    minutes, re-extracting it with gpt-5.4 each time."""
    job_id = 9_999_020
    JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete(synchronize_session=False)
    db.session.commit()
    _mk_row(job_id, edited=None)
    db.session.commit()

    _run_auto_removal(job_id)

    row = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
    assert row is not None, "First absence must not delete the row"
    assert row.tearsheet_absent_since is not None, "First absence must stamp the row"

    JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete(synchronize_session=False)
    db.session.commit()


def test_auto_removal_deletes_after_grace_window(app_ctx):
    """A job absent past the grace window is still cleaned up."""
    job_id = 9_999_021
    JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete(synchronize_session=False)
    db.session.commit()
    _mk_row(job_id, edited=None)
    db.session.commit()

    _run_auto_removal(job_id)

    row = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
    row.tearsheet_absent_since = datetime.utcnow() - timedelta(hours=ABSENCE_GRACE_HOURS + 1)
    db.session.commit()

    _run_auto_removal(job_id)

    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first() is None, \
        "Row absent beyond the grace window must be deleted"


def test_returning_job_loses_its_absence_stamp(app_ctx):
    """A job seen active again must not carry an old stamp into the next
    grace-window check, otherwise it would be deleted despite being active."""
    job_id = 9_999_022
    JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete(synchronize_session=False)
    db.session.commit()
    _mk_row(job_id, edited=None)
    db.session.commit()

    _run_auto_removal(job_id)
    assert JobVettingRequirements.query.filter_by(
        bullhorn_job_id=job_id
    ).first().tearsheet_absent_since is not None

    clear_absence_marks([job_id])

    row = JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first()
    assert row.tearsheet_absent_since is None, "Returning job must lose its stamp"

    JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete(synchronize_session=False)
    db.session.commit()


# -----------------------------------------------------------------------------
# Path 2: sync_requirements_with_active_jobs (every-cycle orphan cleanup)
# -----------------------------------------------------------------------------

def _make_sync_service():
    """Return a vetting service object with just the JobManagementMixin attached
    so we can call sync_requirements_with_active_jobs() in isolation."""
    from screening.job_management import JobManagementMixin

    class _StubSvc(JobManagementMixin):
        def __init__(self):
            pass
    return _StubSvc()


def test_sync_orphan_cleanup_preserves_edited_rows(app_ctx, caplog):
    """When a job is missing from active tearsheets, an orphan row WITH edits
    must survive; an orphan row WITHOUT edits must be deleted."""
    edited_id = 9_999_101  # not in active set, has edits → must survive
    plain_id  = 9_999_102  # not in active set, no edits → must be deleted
    active_id = 9_999_103  # in active set → untouched

    JobVettingRequirements.query.filter(
        JobVettingRequirements.bullhorn_job_id.in_([edited_id, plain_id, active_id])
    ).delete(synchronize_session=False)
    db.session.commit()

    _mk_row(edited_id, edited="- 5+ yrs Python\n- AWS hands-on")
    _mk_row(plain_id,  edited=None)
    _mk_row(active_id, edited=None)
    db.session.commit()

    svc = _make_sync_service()

    # Active tearsheet returns ONLY active_id — edited_id and plain_id are orphans.
    with patch.object(svc, "get_active_jobs_from_tearsheets", return_value=[{"id": active_id}]):
        with caplog.at_level("WARNING"):
            results = svc.sync_requirements_with_active_jobs()

    assert results["success"] is True
    assert results["removed"] == 0, "first absence is debounced, not deleted"
    assert results["marked_absent"] == 1, "the un-edited orphan should be stamped"
    assert results["preserved_edits"] == 1, "one edited orphan should be preserved"

    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=edited_id).first() is not None, \
        "Edited orphan must be preserved across orphan cleanup"
    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=active_id).first() is not None, \
        "Active row must not be touched"

    # Once the grace window has elapsed the orphan is genuinely removed.
    plain_row = JobVettingRequirements.query.filter_by(bullhorn_job_id=plain_id).first()
    assert plain_row is not None, "Plain orphan survives the first pass"
    plain_row.tearsheet_absent_since = datetime.utcnow() - timedelta(
        hours=ABSENCE_GRACE_HOURS + 1
    )
    db.session.commit()

    with patch.object(svc, "get_active_jobs_from_tearsheets", return_value=[{"id": active_id}]):
        results = svc.sync_requirements_with_active_jobs()

    assert results["removed"] == 1, "orphan past the grace window should be deleted"
    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=plain_id).first() is None, \
        "Plain orphan must be deleted once continuously absent past the window"

    protected_log = [r for r in caplog.records if "sync_orphan_edit_protected" in r.message]
    assert any(str(edited_id) in r.message for r in protected_log), \
        "A WARNING log line must identify the protected job_id for ops visibility"

    JobVettingRequirements.query.filter(
        JobVettingRequirements.bullhorn_job_id.in_([edited_id, active_id])
    ).delete(synchronize_session=False)
    db.session.commit()


def test_sync_orphan_cleanup_aborts_on_empty_active_set(app_ctx):
    """Existing safety: if active_jobs comes back empty (likely API failure)
    while we have requirements rows, sync must abort and delete nothing."""
    job_id = 9_999_110
    JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete(synchronize_session=False)
    db.session.commit()
    _mk_row(job_id, edited=None)
    db.session.commit()

    svc = _make_sync_service()
    with patch.object(svc, "get_active_jobs_from_tearsheets", return_value=[]):
        results = svc.sync_requirements_with_active_jobs()

    assert results["success"] is False
    assert results["removed"] == 0
    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first() is not None

    JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete(synchronize_session=False)
    db.session.commit()
