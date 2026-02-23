"""
Core flow tests for CandidateVettingService.

Covers:
  - run_vetting_cycle lifecycle (lock, detect, process, notify, release)
  - Candidate selection from ParsedEmail vs fallback detection
  - CandidateVettingLog creation and status transitions
  - CandidateJobMatch record writing
  - ParsedEmail mark-as-vetted
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock, PropertyMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _set_vetting_enabled(app, enabled: bool):
    from app import db
    from models import VettingConfig
    with app.app_context():
        cfg = VettingConfig.query.filter_by(setting_key='vetting_enabled').first()
        if cfg:
            cfg.setting_value = 'true' if enabled else 'false'
        else:
            db.session.add(VettingConfig(setting_key='vetting_enabled',
                                         setting_value='true' if enabled else 'false'))
        db.session.commit()


def _release_lock(app):
    from app import db
    from models import VettingConfig
    with app.app_context():
        lock = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
        if lock:
            lock.setting_value = 'false'
            db.session.commit()


def _make_cvs(app):
    """Create a CVS instance with mocked Bullhorn."""
    from candidate_vetting_service import CandidateVettingService
    with patch.dict('os.environ', {'OPENAI_API_KEY': 'test-key'}):
        cvs = CandidateVettingService.__new__(CandidateVettingService)
        cvs.bullhorn_service = MagicMock()
        cvs.openai_client = MagicMock()
        cvs.model = 'gpt-4o'
        cvs.embedding_model = 'text-embedding-3-small'
    return cvs


# ===========================================================================
# 1. run_vetting_cycle: disabled → returns immediately
# ===========================================================================
class TestRunVettingCycleDisabled:

    def test_disabled_returns_status_disabled(self, app):
        """When vetting_enabled=false, run_vetting_cycle must return early."""
        _set_vetting_enabled(app, False)
        cvs = _make_cvs(app)

        with app.app_context():
            result = cvs.run_vetting_cycle()

        assert result['status'] == 'disabled'


# ===========================================================================
# 2. run_vetting_cycle: lock prevents concurrent runs
# ===========================================================================
class TestVettingLockBehavior:

    def test_lock_prevents_overlapping_cycles(self, app):
        """If the lock is already held, cycle must return skipped."""
        from app import db
        from models import VettingConfig
        _set_vetting_enabled(app, True)
        cvs = _make_cvs(app)

        with app.app_context():
            # Manually set lock with a recent timestamp so it's not stale
            lock = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
            if lock:
                lock.setting_value = 'true'
            else:
                db.session.add(VettingConfig(setting_key='vetting_in_progress',
                                             setting_value='true'))
            # Set lock_time so it's not auto-released as stale
            lock_time = VettingConfig.query.filter_by(setting_key='vetting_lock_time').first()
            if lock_time:
                lock_time.setting_value = datetime.utcnow().isoformat()
            else:
                db.session.add(VettingConfig(setting_key='vetting_lock_time',
                                             setting_value=datetime.utcnow().isoformat()))
            db.session.commit()

            result = cvs.run_vetting_cycle()

        assert result['status'] == 'skipped'
        assert result.get('reason') == 'cycle_in_progress'
        _release_lock(app)

    def test_lock_released_after_cycle(self, app):
        """Lock must be released even when the cycle completes normally."""
        from app import db
        from models import VettingConfig
        _set_vetting_enabled(app, True)
        cvs = _make_cvs(app)

        with app.app_context():
            _release_lock(app)
            # No candidates → cycle ends quickly
            with patch.object(cvs, 'detect_unvetted_applications', return_value=[]):
                with patch.object(cvs, 'detect_new_applicants', return_value=[]):
                    with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                        cvs.run_vetting_cycle()

            lock = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
            assert lock is None or lock.setting_value == 'false'

    def test_lock_released_on_exception(self, app):
        """Lock must be released even if the cycle throws an exception."""
        from app import db
        from models import VettingConfig
        _set_vetting_enabled(app, True)
        cvs = _make_cvs(app)

        with app.app_context():
            _release_lock(app)
            with patch.object(cvs, 'detect_unvetted_applications',
                              side_effect=RuntimeError("boom")):
                with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                    result = cvs.run_vetting_cycle()

            lock = VettingConfig.query.filter_by(setting_key='vetting_in_progress').first()
            assert lock is None or lock.setting_value == 'false'


# ===========================================================================
# 3. Candidate detection: ParsedEmail primary, Bullhorn fallback
# ===========================================================================
class TestCandidateDetection:

    def test_parsed_email_is_primary_detection(self, app):
        """detect_unvetted_applications is the primary detection method."""
        _set_vetting_enabled(app, True)
        cvs = _make_cvs(app)

        with app.app_context():
            _release_lock(app)
            mock_candidates = [{'id': 100, 'firstName': 'Test', 'lastName': 'User',
                                '_parsed_email_id': 1, '_applied_job_id': 10}]
            with patch.object(cvs, 'detect_unvetted_applications', return_value=mock_candidates) as detect:
                with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                    with patch.object(cvs, 'process_candidate', return_value=None):
                        result = cvs.run_vetting_cycle()

            detect.assert_called_once()
            assert result['detection_method'] == 'parsed_email'
            assert result['candidates_detected'] == 1
            _release_lock(app)

    def test_fallback_to_bullhorn_search(self, app):
        """When no ParsedEmail records, falls back to detect_new_applicants."""
        _set_vetting_enabled(app, True)
        cvs = _make_cvs(app)

        with app.app_context():
            _release_lock(app)
            mock_candidates = [{'id': 200, 'firstName': 'Legacy', 'lastName': 'Candidate'}]
            with patch.object(cvs, 'detect_unvetted_applications', return_value=[]):
                with patch.object(cvs, 'detect_new_applicants', return_value=mock_candidates):
                    with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                        with patch.object(cvs, 'process_candidate', return_value=None):
                            result = cvs.run_vetting_cycle()

            assert result['detection_method'] == 'bullhorn_search'
            assert result['candidates_detected'] == 1
            _release_lock(app)


# ===========================================================================
# 4. CandidateVettingLog creation and status transitions
# ===========================================================================
class TestVettingLogCreation:

    def test_log_created_with_processing_status(self, app):
        """process_candidate creates a log in 'processing' status first."""
        from app import db
        from models import CandidateVettingLog
        _set_vetting_enabled(app, True)
        cvs = _make_cvs(app)

        with app.app_context():
            candidate = {
                'id': 300, 'firstName': 'Jane', 'lastName': 'Doe',
                'email': 'jane@example.com', 'description': 'A' * 200,
                '_parsed_email_id': None, '_applied_job_id': None
            }
            # Mock everything after log creation to isolate
            with patch.object(cvs, 'get_candidate_job_submission', return_value=None):
                with patch.object(cvs, 'get_active_jobs_from_tearsheets', return_value=[]):
                    with patch.object(cvs, '_get_bullhorn_service', return_value=cvs.bullhorn_service):
                        result = cvs.process_candidate(candidate)

            # Should have created a log
            log = CandidateVettingLog.query.filter_by(bullhorn_candidate_id=300).first()
            assert log is not None
            assert log.candidate_name == 'Jane Doe'

            # Cleanup
            CandidateVettingLog.query.filter_by(bullhorn_candidate_id=300).delete()
            db.session.commit()

    def test_no_jobs_produces_completed_log(self, app):
        """When there are no active jobs, candidate is still logged as completed."""
        from app import db
        from models import CandidateVettingLog
        cvs = _make_cvs(app)

        with app.app_context():
            candidate = {
                'id': 301, 'firstName': 'No', 'lastName': 'Jobs',
                'email': 'nojobs@test.com', 'description': 'Experienced developer ' * 30,
                '_parsed_email_id': None, '_applied_job_id': None
            }
            with patch.object(cvs, 'get_candidate_job_submission', return_value=None):
                with patch.object(cvs, 'get_active_jobs_from_tearsheets', return_value=[]):
                    with patch.object(cvs, '_get_bullhorn_service', return_value=cvs.bullhorn_service):
                        result = cvs.process_candidate(candidate)

            log = CandidateVettingLog.query.filter_by(bullhorn_candidate_id=301).first()
            assert log is not None
            assert log.status == 'completed'

            # Cleanup
            CandidateVettingLog.query.filter_by(bullhorn_candidate_id=301).delete()
            db.session.commit()


# ===========================================================================
# 5. ParsedEmail mark-as-vetted
# ===========================================================================
class TestMarkApplicationVetted:

    def test_parsed_email_marked_after_processing(self, app):
        """After _mark_application_vetted, the ParsedEmail record's vetted_at is set."""
        from app import db
        from models import ParsedEmail
        cvs = _make_cvs(app)

        with app.app_context():
            # Create a ParsedEmail record with required fields
            pe = ParsedEmail(
                message_id='test-msg-001',
                sender_email='applicant@test.com',
                recipient_email='inbox@test.com',
                received_at=datetime.utcnow(),
                bullhorn_candidate_id=400,
                candidate_name='Mark Test',
                status='processed',
            )
            db.session.add(pe)
            db.session.commit()
            pe_id = pe.id

            assert pe.vetted_at is None

            # Mark as vetted
            cvs._mark_application_vetted(pe_id)

            pe_after = db.session.get(ParsedEmail, pe_id)
            assert pe_after.vetted_at is not None

            # Cleanup
            db.session.delete(pe_after)
            db.session.commit()


# ===========================================================================
# 6. Empty cycle still updates last_run timestamp
# ===========================================================================
class TestEmptyCycleTimestamp:

    def test_empty_cycle_updates_last_run(self, app):
        """When no candidates are found, last_run_timestamp is still updated."""
        from app import db
        from models import VettingConfig
        _set_vetting_enabled(app, True)
        cvs = _make_cvs(app)

        with app.app_context():
            _release_lock(app)
            before = datetime.utcnow()

            with patch.object(cvs, 'detect_unvetted_applications', return_value=[]):
                with patch.object(cvs, 'detect_new_applicants', return_value=[]):
                    with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                        cvs.run_vetting_cycle()

            ts = VettingConfig.query.filter_by(setting_key='last_run_timestamp').first()
            assert ts is not None
            _release_lock(app)
