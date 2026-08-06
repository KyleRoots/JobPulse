"""Loop guard for the pending-revet detector.

``detect_pending_revet_candidates`` treats the audit log as a durable re-vet
queue: any row still sitting at ``revet_triggered`` / ``revet_new_score=NULL``
gets its candidate's vetting state deleted and re-enqueued for a full
multi-job re-screen.

That recovery is correct only while the row can still back-fill. If the
audited job is no longer in the candidate's scored set, the row never
back-fills and the detector re-screens the same candidate on every cycle
forever. Jul 2026: audit row 18513 re-screened one candidate every ~3 minutes
for a full day before it was caught.

These tests pin the two terminal conditions that stop that loop, plus the
regression guard that a genuinely-recoverable row is still re-enqueued.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

import pytest


@pytest.fixture
def detector():
    from screening.detection import CandidateDetectionMixin

    class Detector(CandidateDetectionMixin):
        pass

    return Detector()


def _seed_audit(app, *, candidate_id, job_id, age_hours=1.0):
    from app import db
    from models import VettingAuditLog

    with app.app_context():
        row = VettingAuditLog(
            candidate_vetting_log_id=candidate_id * 7,
            bullhorn_candidate_id=candidate_id,
            candidate_name='Loop Guard Candidate',
            job_id=job_id,
            job_title='Solution Architect',
            original_score=0.0,
            finding_type='recency_misfire',
            confidence='high',
            action_taken='revet_triggered',
            revet_new_score=None,
            audit_finding='AI confirmed recency misfire.',
            created_at=datetime.utcnow() - timedelta(hours=age_hours),
        )
        db.session.add(row)
        db.session.commit()
        return row.id


def _seed_completed_run(app, *, candidate_id, scores_by_job_id, age_hours=0.1):
    from app import db
    from models import CandidateVettingLog, CandidateJobMatch

    with app.app_context():
        applied_job_id = next(iter(scores_by_job_id))
        vlog = CandidateVettingLog(
            bullhorn_candidate_id=candidate_id,
            candidate_name='Loop Guard Candidate',
            applied_job_id=applied_job_id,
            applied_job_title='Solution Architect',
            status='completed',
            is_qualified=False,
            highest_match_score=max(scores_by_job_id.values()),
            is_sandbox=False,
            analyzed_at=datetime.utcnow(),
            created_at=datetime.utcnow() - timedelta(hours=age_hours),
        )
        db.session.add(vlog)
        db.session.flush()
        for job_id, score in scores_by_job_id.items():
            db.session.add(CandidateJobMatch(
                vetting_log_id=vlog.id,
                bullhorn_job_id=job_id,
                job_title='Solution Architect',
                match_score=score,
                is_qualified=score >= 75,
                is_applied_job=(job_id == applied_job_id),
                match_summary='post-revet summary',
                gaps_identified='',
            ))
        db.session.commit()
        return vlog.id


def _read_audit(app, audit_id):
    from models import VettingAuditLog

    with app.app_context():
        return VettingAuditLog.query.get(audit_id)


@pytest.fixture(autouse=True)
def _clean(app):
    from app import db
    from models import CandidateVettingLog, CandidateJobMatch, VettingAuditLog

    def wipe():
        with app.app_context():
            CandidateJobMatch.query.delete()
            CandidateVettingLog.query.delete()
            VettingAuditLog.query.delete()
            db.session.commit()

    wipe()
    yield
    wipe()


class TestPendingRevetLoopGuard:
    def test_terminates_when_revet_ran_without_audited_job(self, app, detector):
        """The exact production loop: a completed run landed after the audit
        but scored a different job, so revet_new_score can never back-fill."""
        candidate_id = 4652476
        audited_job = 35263
        audit_id = _seed_audit(
            app, candidate_id=candidate_id, job_id=audited_job, age_hours=2.0
        )
        _seed_completed_run(
            app, candidate_id=candidate_id, scores_by_job_id={35515: 42.0}
        )

        with app.app_context():
            with patch(
                'vetting_audit_service.clear_candidate_vetting_state'
            ) as mock_clear:
                enqueued = detector.detect_pending_revet_candidates()

        assert enqueued == []
        mock_clear.assert_not_called()

        row = _read_audit(app, audit_id)
        assert row.action_taken == 'revet_skipped_job_mismatch'
        assert 'can never be back-filled' in row.audit_finding
        assert 'AI confirmed recency misfire.' in row.audit_finding

    def test_terminates_when_older_than_attempt_cap(self, app, detector):
        """No post-audit run at all, but the row has been retrying past the
        age cap — stop before it re-screens indefinitely."""
        candidate_id = 991001
        audit_id = _seed_audit(
            app, candidate_id=candidate_id, job_id=40001, age_hours=30.0
        )

        with app.app_context():
            with patch(
                'vetting_audit_service.clear_candidate_vetting_state'
            ) as mock_clear:
                enqueued = detector.detect_pending_revet_candidates(
                    max_attempt_hours=12.0
                )

        assert enqueued == []
        mock_clear.assert_not_called()

        row = _read_audit(app, audit_id)
        assert row.action_taken == 'revet_skipped_job_mismatch'
        assert 'still un-scored' in row.audit_finding

    def test_recoverable_row_is_still_enqueued(self, app, detector):
        """Regression guard: a recent row with no post-audit run is the case
        this detector exists for and must still be re-enqueued."""
        candidate_id = 991002
        audit_id = _seed_audit(
            app, candidate_id=candidate_id, job_id=40002, age_hours=1.0
        )

        with app.app_context():
            with patch(
                'vetting_audit_service.clear_candidate_vetting_state',
                return_value={'vetting_logs_deleted': 1},
            ):
                enqueued = detector.detect_pending_revet_candidates(
                    max_attempt_hours=12.0
                )

        assert len(enqueued) == 1
        assert enqueued[0]['id'] == candidate_id
        assert enqueued[0]['_pending_revet_audit_id'] == audit_id
        assert enqueued[0]['_applied_job_id'] == 40002

        row = _read_audit(app, audit_id)
        assert row.action_taken == 'revet_triggered'

    def test_backfillable_row_is_left_alone(self, app, detector):
        """A completed run that DID score the audited job is a back-fill
        problem, not a loop — leave the row for backfill_revet_new_score."""
        candidate_id = 991003
        audited_job = 40003
        audit_id = _seed_audit(
            app, candidate_id=candidate_id, job_id=audited_job, age_hours=2.0
        )
        _seed_completed_run(
            app, candidate_id=candidate_id, scores_by_job_id={audited_job: 61.0}
        )

        with app.app_context():
            with patch(
                'vetting_audit_service.clear_candidate_vetting_state'
            ) as mock_clear:
                enqueued = detector.detect_pending_revet_candidates()

        assert enqueued == []
        mock_clear.assert_not_called()

        row = _read_audit(app, audit_id)
        assert row.action_taken == 'revet_triggered'
