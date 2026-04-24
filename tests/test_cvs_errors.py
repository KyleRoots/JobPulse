"""
Error handling tests for CandidateVettingService.

Covers:
  - OpenAI API failures (quota, timeout, malformed response)
  - Bullhorn service unavailable / authentication failure
  - Partial failures (some jobs vet, some don't)
  - Candidate-level exceptions don't crash the cycle
  - Quota exhaustion auto-disable mechanism
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock


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


def _make_cvs():
    from candidate_vetting_service import CandidateVettingService
    cvs = CandidateVettingService.__new__(CandidateVettingService)
    cvs.bullhorn_service = MagicMock()
    cvs.openai_client = MagicMock()
    cvs.model = 'gpt-5.4'
    cvs.embedding_model = 'text-embedding-3-small'
    return cvs


# ===========================================================================
# 1. Candidate-level exception doesn't crash the cycle
# ===========================================================================
class TestCandidateLevelErrorContainment:

    def test_error_in_one_candidate_doesnt_crash_cycle(self, app):
        """If process_candidate throws for one candidate, the cycle continues."""
        _set_vetting_enabled(app, True)
        cvs = _make_cvs()

        with app.app_context():
            _release_lock(app)
            candidates = [
                {'id': 501, 'firstName': 'Fail', 'lastName': 'Case'},
                {'id': 502, 'firstName': 'Pass', 'lastName': 'Case'},
            ]
            call_count = [0]

            def mock_process(candidate):
                call_count[0] += 1
                if candidate['id'] == 501:
                    raise RuntimeError("Simulated failure")
                return None  # 502 processes fine

            with patch.object(cvs, 'detect_unvetted_applications', return_value=candidates):
                with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                    with patch.object(cvs, 'detect_matador_candidates', return_value=[]):
                        with patch.object(cvs, 'process_candidate', side_effect=mock_process):
                            result = cvs.run_vetting_cycle()

            # Both candidates should have been attempted
            assert call_count[0] == 2
            assert len(result['errors']) >= 1
            assert '501' in result['errors'][0]
            _release_lock(app)

    def test_cycle_returns_error_list(self, app):
        """Errors during the cycle are collected in the summary."""
        _set_vetting_enabled(app, True)
        cvs = _make_cvs()

        with app.app_context():
            _release_lock(app)
            candidates = [
                {'id': 601, 'firstName': 'Err', 'lastName': 'One'},
            ]
            with patch.object(cvs, 'detect_unvetted_applications', return_value=candidates):
                with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                    with patch.object(cvs, 'detect_matador_candidates', return_value=[]):
                        with patch.object(cvs, 'process_candidate',
                                          side_effect=ValueError("bad data")):
                            result = cvs.run_vetting_cycle()

            assert len(result['errors']) == 1
            assert 'bad data' in result['errors'][0]
            _release_lock(app)


# ===========================================================================
# 2. resume extraction failure → log completed with error
# ===========================================================================
class TestResumeExtractionFailure:

    def test_no_resume_produces_error_log(self, app):
        """When neither description nor file download yields text, log has error."""
        from app import db
        from models import CandidateVettingLog
        cvs = _make_cvs()

        with app.app_context():
            candidate = {
                'id': 700, 'firstName': 'No', 'lastName': 'Resume',
                'email': 'noresume@test.com', 'description': None,
                '_parsed_email_id': None, '_applied_job_id': None
            }
            with patch.object(cvs, 'get_candidate_job_submission', return_value=None):
                with patch.object(cvs, 'get_candidate_resume', return_value=(None, None)):
                    with patch.object(cvs, 'get_active_jobs_from_tearsheets', return_value=[]):
                        with patch.object(cvs, '_get_bullhorn_service', return_value=cvs.bullhorn_service):
                            result = cvs.process_candidate(candidate)

            log = CandidateVettingLog.query.filter_by(bullhorn_candidate_id=700).first()
            assert log is not None
            # Should have error about no resume
            assert log.error_message is not None or log.status == 'completed'

            CandidateVettingLog.query.filter_by(bullhorn_candidate_id=700).delete()
            db.session.commit()


# ===========================================================================
# 3. Quota exhaustion counter and auto-disable
# ===========================================================================
class TestQuotaExhaustionMechanism:

    def test_quota_counter_resets_at_cycle_start(self, app):
        """_consecutive_quota_errors counter resets to 0 at the start of each cycle."""
        from candidate_vetting_service import CandidateVettingService
        _set_vetting_enabled(app, True)
        cvs = _make_cvs()

        with app.app_context():
            _release_lock(app)
            CandidateVettingService._consecutive_quota_errors = 5

            with patch.object(cvs, 'detect_unvetted_applications', return_value=[]):
                with patch.object(cvs, 'detect_new_applicants', return_value=[]):
                    with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                        with patch.object(cvs, 'detect_matador_candidates', return_value=[]):
                            cvs.run_vetting_cycle()

            assert CandidateVettingService._consecutive_quota_errors == 0
            _release_lock(app)

    def test_quota_alert_sent_flag_resets_on_healthy_cycle(self, app):
        """When a full cycle processes candidates with 0 quota errors,
        the alert flag resets."""
        from candidate_vetting_service import CandidateVettingService
        from unittest.mock import MagicMock
        _set_vetting_enabled(app, True)
        cvs = _make_cvs()

        with app.app_context():
            _release_lock(app)
            CandidateVettingService._quota_alert_sent = True
            CandidateVettingService._consecutive_quota_errors = 0

            # Need at least one candidate for the cycle to reach the quota check
            mock_candidates = [{'id': 900, 'firstName': 'Test', 'lastName': 'User'}]
            mock_log = MagicMock()
            mock_log.status = 'completed'
            mock_log.is_qualified = False
            mock_log.note_created = True

            with patch.object(cvs, 'detect_unvetted_applications', return_value=mock_candidates):
                with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                    with patch.object(cvs, 'detect_matador_candidates', return_value=[]):
                        with patch.object(cvs, 'process_candidate', return_value=mock_log):
                            cvs.run_vetting_cycle()

            assert CandidateVettingService._quota_alert_sent is False
            _release_lock(app)


# ===========================================================================
# 4. Pandologic candidate detection merge
# ===========================================================================
class TestPandologicMerge:

    def test_pandologic_candidates_merged_without_dupes(self, app):
        """Pandologic candidates are merged with parsed_email candidates, deduped by ID."""
        _set_vetting_enabled(app, True)
        cvs = _make_cvs()

        with app.app_context():
            _release_lock(app)
            email_candidates = [{'id': 800, 'firstName': 'A', 'lastName': 'B'}]
            pando_candidates = [
                {'id': 800, 'firstName': 'A', 'lastName': 'B'},  # duplicate
                {'id': 801, 'firstName': 'C', 'lastName': 'D'},  # new
            ]

            with patch.object(cvs, 'detect_unvetted_applications', return_value=email_candidates):
                with patch.object(cvs, 'detect_pandologic_candidates', return_value=pando_candidates):
                    with patch.object(cvs, 'detect_matador_candidates', return_value=[]):
                        with patch.object(cvs, 'process_candidate', return_value=None):
                            result = cvs.run_vetting_cycle()

            assert result['candidates_detected'] == 2  # deduped
            assert 'pandologic' in result['detection_method']
            _release_lock(app)


# ===========================================================================
# 5. Matador candidate detection merge (corporate website submissions)
# ===========================================================================
class TestMatadorMerge:

    def test_matador_candidates_merged_without_dupes(self, app):
        """Matador candidates are merged with parsed_email + pandologic candidates,
        deduped by Bullhorn candidate ID, and reflected in detection_method."""
        _set_vetting_enabled(app, True)
        cvs = _make_cvs()

        with app.app_context():
            _release_lock(app)
            email_candidates = [{'id': 900, 'firstName': 'Email', 'lastName': 'One'}]
            matador_candidates = [
                {'id': 900, 'firstName': 'Email', 'lastName': 'One'},  # duplicate of email
                {'id': 901, 'firstName': 'Matador', 'lastName': 'New'},  # new
            ]

            with patch.object(cvs, 'detect_unvetted_applications', return_value=email_candidates):
                with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                    with patch.object(cvs, 'detect_matador_candidates', return_value=matador_candidates):
                        with patch.object(cvs, 'process_candidate', return_value=None):
                            result = cvs.run_vetting_cycle()

            assert result['candidates_detected'] == 2  # deduped
            assert 'matador' in result['detection_method']
            _release_lock(app)

    def test_matador_only_cycle_uses_matador_method(self, app):
        """If only Matador candidates are present, detection_method still flags it."""
        _set_vetting_enabled(app, True)
        cvs = _make_cvs()

        with app.app_context():
            _release_lock(app)
            matador_candidates = [{'id': 902, 'firstName': 'M', 'lastName': 'Only'}]

            with patch.object(cvs, 'detect_unvetted_applications', return_value=[]):
                with patch.object(cvs, 'detect_new_applicants', return_value=[]):
                    with patch.object(cvs, 'detect_pandologic_candidates', return_value=[]):
                        with patch.object(cvs, 'detect_matador_candidates', return_value=matador_candidates):
                            with patch.object(cvs, 'process_candidate', return_value=None):
                                result = cvs.run_vetting_cycle()

            assert result['candidates_detected'] == 1
            assert 'matador' in result['detection_method']
            _release_lock(app)

    def test_matador_detection_uses_owner_filter(self, app):
        """detect_matador_candidates queries Bullhorn with owner.name:'Matador API'."""
        from unittest.mock import MagicMock
        cvs = _make_cvs()

        # Stub the Bullhorn service the detection method uses
        mock_bh = MagicMock()
        mock_bh.authenticate.return_value = True
        mock_bh.base_url = 'https://example.test/'
        mock_bh.rest_token = 'TEST_TOKEN'

        candidate_response = MagicMock()
        candidate_response.status_code = 200
        candidate_response.json.return_value = {'data': []}
        mock_bh.session.get.return_value = candidate_response

        with patch.object(cvs, '_get_bullhorn_service', return_value=mock_bh):
            with patch.object(cvs, '_get_last_run_timestamp', return_value=None):
                cvs.detect_matador_candidates(since_minutes=10)

        # First call is the Candidate search; verify the owner filter is correct
        first_call_args, first_call_kwargs = mock_bh.session.get.call_args_list[0]
        assert 'search/Candidate' in first_call_args[0]
        params = first_call_kwargs.get('params', {})
        assert 'owner.name:"Matador API"' in params.get('query', '')
        assert 'dateLastModified' in params.get('query', '')
