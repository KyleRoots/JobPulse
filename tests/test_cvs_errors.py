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


# ===========================================================================
# 6. JobSubmission lookup retry helper (Task B)
# ===========================================================================
class TestJobSubmissionLookupRetry:
    """
    Tests for CandidateDetectionMixin._fetch_latest_job_submission, the shared
    helper used by both Pandologic and Matador detectors. Behavior:
      - Retries once on transient failures (5xx, network errors, JSON parse)
      - Logs a WARNING on persistent failure (so operators see missed lookups)
      - Returns (None, None, True) on legitimate empty submissions
      - Returns (None, None, False) on persistent failure (caller falls back
        to global dedup, candidate is NOT dropped)
    """

    def _make_bullhorn(self):
        from unittest.mock import MagicMock
        bh = MagicMock()
        bh.base_url = 'https://example.test/'
        bh.rest_token = 'TEST_TOKEN'
        return bh

    def _make_response(self, status_code, json_data=None, raise_value_error=False):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = status_code
        if raise_value_error:
            resp.json.side_effect = ValueError("not json")
        else:
            resp.json.return_value = json_data or {}
        return resp

    def test_success_returns_id_and_title(self):
        """200 with a submission → (id, title, True)."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.return_value = self._make_response(
            200,
            {'data': [{'id': 1, 'jobOrder': {'id': 555, 'title': 'Senior Dev'},
                       'dateAdded': 0}]},
        )

        job_id, title, ok = cvs._fetch_latest_job_submission(bh, 12345)

        assert job_id == 555
        assert title == 'Senior Dev'
        assert ok is True
        assert bh.session.get.call_count == 1  # no retry needed

    def test_empty_submissions_returns_none_with_success(self):
        """200 with no submissions → (None, None, True). NOT a failure."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.return_value = self._make_response(200, {'data': []})

        job_id, title, ok = cvs._fetch_latest_job_submission(bh, 12345)

        assert job_id is None
        assert title is None
        assert ok is True  # legitimate empty result, not a transient failure
        assert bh.session.get.call_count == 1

    def test_5xx_then_success_retries_once(self):
        """500 → retry → 200 with submission. Returns the data."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.side_effect = [
            self._make_response(503),
            self._make_response(200, {'data': [
                {'id': 2, 'jobOrder': {'id': 777, 'title': 'PM'}}
            ]}),
        ]

        with patch('screening.detection.time.sleep') as mock_sleep:
            job_id, title, ok = cvs._fetch_latest_job_submission(bh, 99)

        assert job_id == 777
        assert title == 'PM'
        assert ok is True
        assert bh.session.get.call_count == 2
        mock_sleep.assert_called_once_with(1)  # backoff between attempts

    def test_5xx_then_5xx_returns_failure_and_warns(self, caplog):
        """500 → retry → still 500. Returns (None, None, False) + WARNING."""
        import logging
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.return_value = self._make_response(502)

        with patch('screening.detection.time.sleep'):
            with caplog.at_level(logging.WARNING, logger='root'):
                job_id, title, ok = cvs._fetch_latest_job_submission(bh, 4242)

        assert job_id is None
        assert title is None
        assert ok is False
        assert bh.session.get.call_count == 2
        warned = [r for r in caplog.records
                  if r.levelno == logging.WARNING
                  and 'JobSubmission lookup failed' in r.getMessage()
                  and '4242' in r.getMessage()]
        assert warned, "Expected WARNING with candidate id 4242"

    def test_timeout_then_success(self):
        """Timeout → retry → 200 success."""
        import requests
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.side_effect = [
            requests.Timeout("timed out"),
            self._make_response(200, {'data': [
                {'id': 3, 'jobOrder': {'id': 888, 'title': 'QA'}}
            ]}),
        ]

        with patch('screening.detection.time.sleep'):
            job_id, title, ok = cvs._fetch_latest_job_submission(bh, 7)

        assert job_id == 888
        assert title == 'QA'
        assert ok is True
        assert bh.session.get.call_count == 2

    def test_timeout_then_timeout_returns_failure_and_warns(self, caplog):
        """Timeout → retry → timeout. Returns failure + WARNING."""
        import logging
        import requests
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.side_effect = requests.Timeout("timed out")

        with patch('screening.detection.time.sleep'):
            with caplog.at_level(logging.WARNING, logger='root'):
                job_id, title, ok = cvs._fetch_latest_job_submission(bh, 8888)

        assert job_id is None
        assert title is None
        assert ok is False
        assert bh.session.get.call_count == 2
        warned = [r for r in caplog.records
                  if r.levelno == logging.WARNING
                  and 'JobSubmission lookup failed' in r.getMessage()
                  and '8888' in r.getMessage()
                  and 'Timeout' in r.getMessage()]
        assert warned, "Expected WARNING with Timeout cause for candidate 8888"

    def test_connection_error_then_success(self):
        """ConnectionError → retry → success."""
        import requests
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.side_effect = [
            requests.ConnectionError("conn refused"),
            self._make_response(200, {'data': [
                {'id': 4, 'jobOrder': {'id': 111, 'title': 'Eng'}}
            ]}),
        ]

        with patch('screening.detection.time.sleep'):
            job_id, title, ok = cvs._fetch_latest_job_submission(bh, 11)

        assert job_id == 111
        assert ok is True

    def test_4xx_does_not_retry(self):
        """401/403/404 should NOT retry (not transient). Returns failure + WARN."""
        import logging
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.return_value = self._make_response(401)

        with patch('screening.detection.time.sleep') as mock_sleep:
            job_id, title, ok = cvs._fetch_latest_job_submission(bh, 333)

        assert ok is False
        assert bh.session.get.call_count == 1  # no retry on 4xx
        mock_sleep.assert_not_called()

    def test_json_parse_error_retries(self):
        """200 with malformed JSON → retry → success."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.side_effect = [
            self._make_response(200, raise_value_error=True),
            self._make_response(200, {'data': [
                {'id': 5, 'jobOrder': {'id': 222, 'title': 'Ops'}}
            ]}),
        ]

        with patch('screening.detection.time.sleep'):
            job_id, title, ok = cvs._fetch_latest_job_submission(bh, 44)

        assert job_id == 222
        assert ok is True
        assert bh.session.get.call_count == 2

    def test_missing_job_order_returns_none_id(self):
        """200 with submission but no jobOrder → returns (None, '', True)."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.return_value = self._make_response(
            200, {'data': [{'id': 1, 'jobOrder': None}]}
        )

        job_id, title, ok = cvs._fetch_latest_job_submission(bh, 55)

        assert job_id is None
        assert title == ''
        assert ok is True


# ===========================================================================
# 7. Recruiter-activity gate (Task D)
# ===========================================================================
class TestRecruiterActivityGate:
    """
    Tests for the recruiter-activity gate that pauses auto-vetting when a
    real human (not the API user) has added a Bullhorn Note to the candidate
    within the configured lookback window.

    Helper under test: CandidateDetectionMixin._has_recent_recruiter_activity
    Wrapper under test: CandidateDetectionMixin._is_paused_by_recruiter_activity
    Integration:        CandidateDetectionMixin._should_skip_candidate(... bullhorn=)
    """

    API_USER_ID = 1147490

    def _make_bullhorn(self):
        from unittest.mock import MagicMock
        bh = MagicMock()
        bh.base_url = 'https://example.test/'
        bh.rest_token = 'TEST_TOKEN'
        bh.user_id = self.API_USER_ID
        return bh

    def _make_response(self, status_code, json_data=None, raise_value_error=False):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = status_code
        if raise_value_error:
            resp.json.side_effect = ValueError("not json")
        else:
            resp.json.return_value = json_data or {}
        return resp

    def _set_config(self, app, key, value):
        from app import db
        from models import VettingConfig
        with app.app_context():
            cfg = VettingConfig.query.filter_by(setting_key=key).first()
            if cfg:
                cfg.setting_value = str(value)
            else:
                db.session.add(VettingConfig(setting_key=key,
                                             setting_value=str(value)))
            db.session.commit()

    # ---- _has_recent_recruiter_activity ------------------------------------

    def test_recent_recruiter_note_returns_active(self, app):
        """A human-authored note in window → (True, minutes_ago)."""
        from datetime import datetime
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        # Note authored 5 min ago by a different user
        five_min_ago_ms = int((datetime.utcnow().timestamp() - 300) * 1000)
        bh.session.get.return_value = self._make_response(200, {
            'data': [
                {'id': 99, 'dateAdded': five_min_ago_ms,
                 'commentingPerson': {'id': 222333}}  # not the API user
            ]
        })

        with app.app_context():
            active, minutes_ago = cvs._has_recent_recruiter_activity(bh, 4654705, 60)

        assert active is True
        assert minutes_ago is not None
        assert 4 <= minutes_ago <= 6  # allow tiny clock skew

    def test_only_ai_notes_returns_inactive(self, app):
        """All notes in window are by the API user → (False, None)."""
        from datetime import datetime
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        recent_ms = int((datetime.utcnow().timestamp() - 60) * 1000)
        bh.session.get.return_value = self._make_response(200, {
            'data': [
                {'id': 1, 'dateAdded': recent_ms,
                 'commentingPerson': {'id': self.API_USER_ID}},
                {'id': 2, 'dateAdded': recent_ms,
                 'commentingPerson': {'id': self.API_USER_ID}},
            ]
        })

        with app.app_context():
            active, minutes_ago = cvs._has_recent_recruiter_activity(bh, 999, 60)

        assert active is False
        assert minutes_ago is None

    def test_no_notes_returns_inactive(self, app):
        """No notes in window → (False, None)."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.return_value = self._make_response(200, {'data': []})

        with app.app_context():
            active, minutes_ago = cvs._has_recent_recruiter_activity(bh, 1000, 60)

        assert active is False
        assert minutes_ago is None

    def test_unknown_author_treated_as_recruiter(self, app):
        """Note with no commentingPerson.id → treat as recruiter (conservative)."""
        from datetime import datetime
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        recent_ms = int((datetime.utcnow().timestamp() - 120) * 1000)
        bh.session.get.return_value = self._make_response(200, {
            'data': [{'id': 1, 'dateAdded': recent_ms, 'commentingPerson': None}]
        })

        with app.app_context():
            active, _ = cvs._has_recent_recruiter_activity(bh, 1, 60)

        assert active is True

    def test_5xx_then_success_retries(self, app):
        """500 → retry → 200 with recruiter note → active."""
        from datetime import datetime
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        recent_ms = int((datetime.utcnow().timestamp() - 60) * 1000)
        bh.session.get.side_effect = [
            self._make_response(503),
            self._make_response(200, {'data': [
                {'id': 1, 'dateAdded': recent_ms,
                 'commentingPerson': {'id': 99999}}
            ]}),
        ]

        with patch('screening.detection.time.sleep'):
            with app.app_context():
                active, _ = cvs._has_recent_recruiter_activity(bh, 1, 60)

        assert active is True
        assert bh.session.get.call_count == 2

    def test_persistent_failure_fails_open_with_warning(self, app, caplog):
        """5xx → retry → 5xx → fail open + WARNING."""
        import logging
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.return_value = self._make_response(502)

        with patch('screening.detection.time.sleep'):
            with app.app_context():
                with caplog.at_level(logging.WARNING, logger='root'):
                    active, _ = cvs._has_recent_recruiter_activity(bh, 7777, 60)

        assert active is False  # fail-open: don't block the vet
        warned = [r for r in caplog.records
                  if r.levelno == logging.WARNING
                  and 'Recruiter-activity lookup failed' in r.getMessage()
                  and '7777' in r.getMessage()]
        assert warned, "Expected WARNING with candidate id 7777"

    def test_4xx_does_not_retry(self, app):
        """4xx (e.g. 401) is non-transient — no retry, fail open."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.return_value = self._make_response(401)

        with patch('screening.detection.time.sleep') as mock_sleep:
            with app.app_context():
                active, _ = cvs._has_recent_recruiter_activity(bh, 1, 60)

        assert active is False
        assert bh.session.get.call_count == 1
        mock_sleep.assert_not_called()

    def test_no_user_id_skips_check(self, app):
        """If bullhorn.user_id is unset we can't distinguish AI from human → fail open."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.user_id = None

        with app.app_context():
            active, _ = cvs._has_recent_recruiter_activity(bh, 1, 60)

        assert active is False
        assert bh.session.get.call_count == 0  # short-circuit, no API call

    # ---- _is_paused_by_recruiter_activity (wrapper + config) --------------

    def test_killswitch_disabled_bypasses_check(self, app):
        """recruiter_activity_check_enabled=false → bypass entirely."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        self._set_config(app, 'recruiter_activity_check_enabled', 'false')

        with app.app_context():
            paused = cvs._is_paused_by_recruiter_activity(bh, 12345)

        assert paused is False
        assert bh.session.get.call_count == 0  # never queried Bullhorn
        self._set_config(app, 'recruiter_activity_check_enabled', 'true')

    def test_lookback_zero_disables_check(self, app):
        """recruiter_activity_lookback_minutes=0 → bypass."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        self._set_config(app, 'recruiter_activity_lookback_minutes', '0')

        with app.app_context():
            paused = cvs._is_paused_by_recruiter_activity(bh, 12345)

        assert paused is False
        assert bh.session.get.call_count == 0
        self._set_config(app, 'recruiter_activity_lookback_minutes', '60')

    def test_no_bullhorn_returns_false(self, app):
        """bullhorn=None → wrapper short-circuits to False."""
        cvs = _make_cvs()
        with app.app_context():
            assert cvs._is_paused_by_recruiter_activity(None, 1) is False

    def test_active_recruiter_pauses(self, app, caplog):
        """End-to-end wrapper: recruiter active → returns True + INFO log."""
        import logging
        from datetime import datetime
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        recent_ms = int((datetime.utcnow().timestamp() - 600) * 1000)  # 10 min ago
        bh.session.get.return_value = self._make_response(200, {
            'data': [{'id': 1, 'dateAdded': recent_ms,
                      'commentingPerson': {'id': 88888}}]
        })

        with app.app_context():
            with caplog.at_level(logging.INFO, logger='root'):
                paused = cvs._is_paused_by_recruiter_activity(bh, 4654705)

        assert paused is True
        infos = [r for r in caplog.records
                 if r.levelno == logging.INFO
                 and 'recruiter activity within' in r.getMessage()
                 and '4654705' in r.getMessage()]
        assert infos, "Expected INFO log for paused candidate"

    # ---- _should_skip_candidate integration -------------------------------

    def test_skip_candidate_no_bullhorn_keeps_db_only_dedup(self, app):
        """Without bullhorn, _should_skip_candidate uses DB-only dedup (back-compat)."""
        cvs = _make_cvs()
        # No vetting log → not in 24h dedup → would normally proceed
        with app.app_context():
            # bullhorn omitted → wrapper short-circuits → returns False
            assert cvs._should_skip_candidate(99001, applied_job_id=42) is False

    def test_skip_candidate_with_bullhorn_blocks_when_recruiter_active(self, app):
        """With bullhorn AND recruiter recently active, candidate is skipped
        even though DB-only dedup would let them through."""
        from datetime import datetime
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        recent_ms = int((datetime.utcnow().timestamp() - 300) * 1000)
        bh.session.get.return_value = self._make_response(200, {
            'data': [{'id': 1, 'dateAdded': recent_ms,
                      'commentingPerson': {'id': 77777}}]
        })

        with app.app_context():
            skip = cvs._should_skip_candidate(99002, applied_job_id=42, bullhorn=bh)

        assert skip is True

    def test_skip_candidate_with_bullhorn_allows_when_no_activity(self, app):
        """With bullhorn but NO recruiter notes, candidate proceeds."""
        cvs = _make_cvs()
        bh = self._make_bullhorn()
        bh.session.get.return_value = self._make_response(200, {'data': []})

        with app.app_context():
            skip = cvs._should_skip_candidate(99003, applied_job_id=42, bullhorn=bh)

        assert skip is False

    def test_dedup_short_circuits_before_recruiter_check(self, app):
        """If existing dedup says skip, we should NOT spend a Bullhorn call
        on the recruiter check — performance-critical path."""
        from app import db
        from models import CandidateVettingLog
        from datetime import datetime
        cvs = _make_cvs()
        bh = self._make_bullhorn()

        with app.app_context():
            # Seed a recent same-job vetting log → triggers Rule 1 (24h skip)
            log = CandidateVettingLog(
                bullhorn_candidate_id=99004,
                applied_job_id=42,
                status='completed',
                created_at=datetime.utcnow(),
            )
            db.session.add(log)
            db.session.commit()

            skip = cvs._should_skip_candidate(99004, applied_job_id=42, bullhorn=bh)

        assert skip is True
        # The recruiter-activity Bullhorn call should NEVER fire
        assert bh.session.get.call_count == 0
