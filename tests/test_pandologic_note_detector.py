"""
Unit tests for the PandoLogic note-based detector.

Background: existing Bullhorn candidates who re-apply via PandoLogic do NOT
get their parent Candidate.owner flipped to 'Pandologic API'. The owner-based
detector only catches brand-new candidates, so re-applicants would otherwise
fall through every other channel (no email forward, no status flip, no owner
change). This detector closes that gap by watching for fresh Notes authored
by the PandoLogic API CorporateUser.
"""
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from screening.detection import CandidateDetectionMixin


class _Detector(CandidateDetectionMixin):
    """Concrete subclass for testing the mixin in isolation."""

    def __init__(self, bullhorn=None, last_run=None):
        self._bullhorn = bullhorn
        self._last_run = last_run

    def _get_bullhorn_service(self):
        return self._bullhorn

    def _get_last_run_timestamp(self):
        return self._last_run


def _make_bullhorn(note_payload, *, sub_payload=None, user_payload=None):
    """Build a fake authenticated Bullhorn service whose .session.get returns
    different payloads depending on the URL being called."""
    bh = MagicMock()
    bh.base_url = 'https://rest45.example/'
    bh.rest_token = 'token-xyz'
    bh.user_id = 1147490
    bh.authenticate.return_value = True

    def _get(url, params=None, timeout=None):
        resp = MagicMock()
        resp.status_code = 200
        if 'search/Note' in url:
            # Differentiate Pandologic-note search from recruiter-activity
            # search by inspecting the query string.
            q = (params or {}).get('query', '')
            if 'commentingPerson.id' in q:
                resp.json.return_value = note_payload
            else:
                # Recruiter-activity check — return an empty list (no human notes)
                resp.json.return_value = {'data': []}
        elif 'search/JobSubmission' in url:
            resp.json.return_value = sub_payload or {'data': []}
        elif 'query/CorporateUser' in url:
            resp.json.return_value = user_payload or {
                'data': [{'id': 999001, 'name': 'Pandologic API'}]
            }
        else:
            resp.json.return_value = {'data': []}
        return resp

    bh.session.get.side_effect = _get
    return bh


# ── _resolve_pandologic_user_id ──────────────────────────────────────────────


def test_resolve_user_id_from_cache(app):
    """Cached value short-circuits the Bullhorn lookup."""
    with app.app_context():
        from models import VettingConfig
        VettingConfig.set_value('pandologic_api_user_id', '999001')

        bh = _make_bullhorn({'data': []})
        det = _Detector(bullhorn=bh)
        result = det._resolve_pandologic_user_id(bh)

        assert result == 999001
        bh.session.get.assert_not_called()  # cache hit, no API call


def test_resolve_user_id_first_run_caches(app):
    """First run hits Bullhorn, persists the ID, and returns it."""
    with app.app_context():
        from models import VettingConfig
        # Ensure no cached value
        existing = VettingConfig.query.filter_by(setting_key='pandologic_api_user_id').first()
        if existing:
            from app import db
            db.session.delete(existing)
            db.session.commit()

        bh = _make_bullhorn({'data': []})
        det = _Detector(bullhorn=bh)
        result = det._resolve_pandologic_user_id(bh)

        assert result == 999001
        # Should now be cached
        cached = VettingConfig.get_value('pandologic_api_user_id')
        assert cached == '999001'


def test_resolve_user_id_returns_none_on_404(app):
    """If Bullhorn doesn't have the user, resolver returns None and detector
    will no-op for this cycle (safe fallback)."""
    with app.app_context():
        from models import VettingConfig
        from app import db
        existing = VettingConfig.query.filter_by(setting_key='pandologic_api_user_id').first()
        if existing:
            db.session.delete(existing)
            db.session.commit()

        bh = _make_bullhorn({'data': []}, user_payload={'data': []})
        det = _Detector(bullhorn=bh)
        result = det._resolve_pandologic_user_id(bh)

        assert result is None


# ── detect_pandologic_note_candidates ────────────────────────────────────────


def test_detector_picks_up_reapplicant_via_note(app):
    """Happy path: a new note from the PandoLogic API user produces a candidate
    in the queue, with applied_job_id enriched from the JobSubmission lookup."""
    with app.app_context():
        from models import VettingConfig
        VettingConfig.set_value('pandologic_api_user_id', '999001')

        note_payload = {
            'data': [
                {
                    'id': 5001,
                    'dateAdded': int(datetime.utcnow().timestamp() * 1000),
                    'personReference': {
                        'id': 4133209,
                        'firstName': 'Oluwadamilare',
                        'lastName': 'Akinwole',
                        'email': 'olu@example.com',
                        'phone': '555-0100',
                        'status': 'Active',
                        'source': 'Indeed Job Board',
                    },
                }
            ]
        }
        sub_payload = {
            'data': [
                {
                    'id': 7777,
                    'jobOrder': {'id': 34986, 'title': 'Finance & Accounting Manager'},
                    'dateAdded': int(datetime.utcnow().timestamp() * 1000),
                }
            ]
        }
        bh = _make_bullhorn(note_payload, sub_payload=sub_payload)
        det = _Detector(bullhorn=bh, last_run=datetime.utcnow() - timedelta(minutes=5))

        result = det.detect_pandologic_note_candidates(since_minutes=5)

        assert len(result) == 1
        cand = result[0]
        assert cand['id'] == 4133209
        assert cand['firstName'] == 'Oluwadamilare'
        assert cand['lastName'] == 'Akinwole'
        assert cand['_applied_job_id'] == 34986
        assert cand['_applied_job_title'] == 'Finance & Accounting Manager'


def test_detector_dedups_multiple_notes_for_same_candidate(app):
    """If the same candidate has multiple PandoLogic notes in the window
    (e.g. they applied to two jobs in the same minute), only emit one
    candidate — downstream dedup handles per-job anyway."""
    with app.app_context():
        from models import VettingConfig
        VettingConfig.set_value('pandologic_api_user_id', '999001')

        now_ms = int(datetime.utcnow().timestamp() * 1000)
        note_payload = {
            'data': [
                {
                    'id': 5001,
                    'dateAdded': now_ms,
                    'personReference': {'id': 4133209, 'firstName': 'Olu', 'lastName': 'A'},
                },
                {
                    'id': 5002,
                    'dateAdded': now_ms - 1000,
                    'personReference': {'id': 4133209, 'firstName': 'Olu', 'lastName': 'A'},
                },
            ]
        }
        bh = _make_bullhorn(note_payload)
        det = _Detector(bullhorn=bh, last_run=datetime.utcnow() - timedelta(minutes=5))

        result = det.detect_pandologic_note_candidates()
        assert len(result) == 1
        assert result[0]['id'] == 4133209


def test_detector_returns_empty_when_user_id_unresolved(app):
    """When the PandoLogic API CorporateUser cannot be resolved, detector
    must safely no-op rather than crashing or doing a wide search."""
    with app.app_context():
        from models import VettingConfig
        from app import db
        existing = VettingConfig.query.filter_by(setting_key='pandologic_api_user_id').first()
        if existing:
            db.session.delete(existing)
            db.session.commit()

        bh = _make_bullhorn({'data': []}, user_payload={'data': []})
        det = _Detector(bullhorn=bh, last_run=datetime.utcnow() - timedelta(minutes=5))

        result = det.detect_pandologic_note_candidates()
        assert result == []


def test_detector_respects_dedup(app):
    """If a candidate was already vetted for the applied job within 24h,
    _should_skip_candidate returns True and the candidate is filtered out."""
    with app.app_context():
        from models import VettingConfig, CandidateVettingLog
        from app import db
        VettingConfig.set_value('pandologic_api_user_id', '999001')

        # Seed a vetting log so dedup fires
        log = CandidateVettingLog(
            bullhorn_candidate_id=4133209,
            candidate_name='Olu A',
            applied_job_id=34986,
            status='completed',
            created_at=datetime.utcnow() - timedelta(hours=1),
        )
        db.session.add(log)
        db.session.commit()

        try:
            now_ms = int(datetime.utcnow().timestamp() * 1000)
            note_payload = {
                'data': [{
                    'id': 5001,
                    'dateAdded': now_ms,
                    'personReference': {'id': 4133209, 'firstName': 'Olu', 'lastName': 'A'},
                }]
            }
            sub_payload = {
                'data': [{
                    'id': 7777,
                    'jobOrder': {'id': 34986, 'title': 'Finance & Accounting Manager'},
                    'dateAdded': now_ms,
                }]
            }
            bh = _make_bullhorn(note_payload, sub_payload=sub_payload)
            det = _Detector(bullhorn=bh, last_run=datetime.utcnow() - timedelta(minutes=5))

            result = det.detect_pandologic_note_candidates()
            assert result == []
        finally:
            db.session.delete(log)
            db.session.commit()


def test_detector_returns_empty_on_search_failure(app):
    """A non-200 from the Note search returns an empty list, not a crash."""
    with app.app_context():
        from models import VettingConfig
        VettingConfig.set_value('pandologic_api_user_id', '999001')

        bh = MagicMock()
        bh.base_url = 'https://rest45.example/'
        bh.rest_token = 'token'
        bh.user_id = 1147490
        bh.authenticate.return_value = True

        def _get(url, params=None, timeout=None):
            resp = MagicMock()
            if 'search/Note' in url:
                resp.status_code = 500
                resp.json.return_value = {}
            else:
                resp.status_code = 200
                resp.json.return_value = {'data': []}
            return resp

        bh.session.get.side_effect = _get
        det = _Detector(bullhorn=bh, last_run=datetime.utcnow() - timedelta(minutes=5))

        result = det.detect_pandologic_note_candidates()
        assert result == []
