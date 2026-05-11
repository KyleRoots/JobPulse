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
from datetime import datetime
from unittest.mock import patch
from app import app, db
from models import JobVettingRequirements
from incremental_monitoring_service import IncrementalMonitoringService


@pytest.fixture
def app_ctx():
    with app.app_context():
        yield


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
    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=plain_id).first() is None, \
        "Non-edited row must be deleted"
    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=other_id).first() is not None, \
        "Unrelated row must not be touched"

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


def test_auto_removal_deletes_when_no_edits_present(app_ctx):
    """Vanilla case: no edits → cleanup proceeds normally."""
    job_id = 9_999_020
    JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).delete(synchronize_session=False)
    db.session.commit()
    _mk_row(job_id, edited=None)
    db.session.commit()

    svc = IncrementalMonitoringService.__new__(IncrementalMonitoringService)
    svc.logger = __import__("logging").getLogger("test_auto_removal_guard")
    svc.auto_removed_jobs = [
        {"job_id": job_id, "job_title": "Plain", "tearsheet_id": 1, "reason": "test"},
    ]

    svc._log_auto_removal_activity()

    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=job_id).first() is None


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
    assert results["removed"] == 1, "exactly one un-edited orphan should be deleted"
    assert results["preserved_edits"] == 1, "one edited orphan should be preserved"

    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=edited_id).first() is not None, \
        "Edited orphan must be preserved across orphan cleanup"
    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=plain_id).first() is None, \
        "Plain orphan must be deleted"
    assert JobVettingRequirements.query.filter_by(bullhorn_job_id=active_id).first() is not None, \
        "Active row must not be touched"

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
