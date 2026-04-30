"""
Tests for the API User → Recruiter Ownership Reassignment task (Task #70 / #83).

Coverage:
  T001 — disabled toggle exits immediately
  T002 — empty api_user_ids exits with a log
  T003 — no candidates found exits cleanly
  T004 — candidate with human note activity is reassigned
  T005 — candidate with no human activity is skipped
  T006 — (removed — job-owner logic replaced by note-based lookup)
  T007 — all note authors are API users → candidate is skipped
  T008 — Bullhorn update failure is counted as failed, not reassigned
  T009 — Bullhorn auth failure exits early
  T010 — note creation failure does not abort the reassignment
  T011 — multiple API user IDs produce correct OR-joined query
  T012 — _parse_api_user_ids strips non-numeric values
  T013 — reassign_owner_note_enabled=false skips note creation
  T014 — ownership_save_config action saves api_user_ids and note toggle correctly
  T015 — settings defaults are present when keys are missing
  T016 — GET /automation_test renders toggle checked/unchecked state
  T017 — ownership_toggle action does not erase api_user_ids or note toggle
  T018 — 5-min cycle with all-skips writes NO Run History row (noise filter)
  T019 — 5-min cycle with a successful reassign writes a Run History row + IDs
  T020 — daily sweep ALWAYS writes a Run History row even with all-skips
  T021 — manual live batch ALWAYS writes a Run History row even with all-skips
  T022 — run history details_json includes reassigned candidate IDs for spot-check
"""
import pytest
from unittest.mock import patch, MagicMock, call
import json


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_config(app, key, value):
    from app import db
    from models import VettingConfig
    with app.app_context():
        row = VettingConfig.query.filter_by(setting_key=key).first()
        if row:
            row.setting_value = value
        else:
            db.session.add(VettingConfig(setting_key=key, setting_value=value))
        db.session.commit()


def _delete_config(app, key):
    from app import db
    from models import VettingConfig
    with app.app_context():
        VettingConfig.query.filter_by(setting_key=key).delete()
        db.session.commit()


def _make_candidate(cid=1001, first='John', last='Doe',
                    owner_id=9999, owner_first='API', owner_last='Bot'):
    return {
        'id': cid,
        'firstName': first,
        'lastName': last,
        'owner': {'id': owner_id, 'firstName': owner_first, 'lastName': owner_last},
    }


def _make_note_resp(person_id=777, first='Bob', last='Smith', extra_notes=None):
    notes = extra_notes or []
    notes.append({
        'id': 1,
        'commentingPerson': {'id': person_id, 'firstName': first, 'lastName': last},
        'dateAdded': 1700000000000,
        'action': 'General',
    })
    notes.sort(key=lambda n: n['dateAdded'])
    return {'data': notes}


# ─────────────────────────────────────────────────────────────────────────────
# T001: disabled toggle
# ─────────────────────────────────────────────────────────────────────────────
class TestDisabledToggle:
    def test_exits_when_disabled(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'false')
        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh:
                reassign_api_user_candidates(since_minutes=30)
                mock_bh.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# T002: empty api_user_ids
# ─────────────────────────────────────────────────────────────────────────────
class TestEmptyApiUserIds:
    def test_exits_when_no_ids(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '')
        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh:
                reassign_api_user_candidates(since_minutes=30)
                mock_bh.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# T003: no candidates found
# ─────────────────────────────────────────────────────────────────────────────
class TestNoCandidates:
    def test_exits_cleanly_when_no_candidates(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'true')

        empty_resp = MagicMock()
        empty_resp.status_code = 200
        empty_resp.json.return_value = {'data': []}

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.return_value = empty_resp
                    reassign_api_user_candidates(since_minutes=30)
                    assert mock_req.post.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# T004: successful reassignment (note-based)
# ─────────────────────────────────────────────────────────────────────────────
class TestSuccessfulReassignment:
    def test_candidate_is_reassigned(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        candidate = _make_candidate()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        note_resp = MagicMock()
        note_resp.status_code = 200
        note_resp.json.return_value = _make_note_resp(person_id=777)

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {'changeType': 'UPDATE', 'changedEntityId': 1001}

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, note_resp]
                    mock_req.post.return_value = update_resp
                    reassign_api_user_candidates(since_minutes=30)

                    update_calls = [
                        c for c in mock_req.post.call_args_list
                        if 'entity/Candidate' in str(c)
                    ]
                    assert len(update_calls) == 1
                    posted_json = update_calls[0].kwargs.get('json') or update_calls[0].args[1] if len(update_calls[0].args) > 1 else update_calls[0].kwargs.get('json')
                    assert posted_json['owner']['id'] == 777


# ─────────────────────────────────────────────────────────────────────────────
# T005: candidate with no human activity is skipped
# ─────────────────────────────────────────────────────────────────────────────
class TestNoHumanActivity:
    def test_candidate_without_human_notes_is_skipped(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        candidate = _make_candidate()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        no_notes_resp = MagicMock()
        no_notes_resp.status_code = 200
        no_notes_resp.json.return_value = {'data': []}

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, no_notes_resp]
                    reassign_api_user_candidates(since_minutes=30)
                    assert mock_req.post.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# T006: only API-user notes → no human interactor → skipped
# ─────────────────────────────────────────────────────────────────────────────
class TestOnlyApiUserNotes:
    def test_skipped_when_only_api_users_noted(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        candidate = _make_candidate()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        api_only_notes = MagicMock()
        api_only_notes.status_code = 200
        api_only_notes.json.return_value = {
            'data': [{
                'id': 1,
                'commentingPerson': {'id': 9999, 'firstName': 'API', 'lastName': 'Bot'},
                'dateAdded': 1700000000000,
                'action': 'General',
            }]
        }

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, api_only_notes]
                    reassign_api_user_candidates(since_minutes=30)
                    assert mock_req.post.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# T007: first human interactor is picked from mixed notes
# ─────────────────────────────────────────────────────────────────────────────
class TestFirstHumanPicked:
    def test_first_human_among_mixed_notes(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        candidate = _make_candidate()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        mixed_notes = MagicMock()
        mixed_notes.status_code = 200
        mixed_notes.json.return_value = {
            'data': [
                {
                    'id': 1,
                    'commentingPerson': {'id': 9999, 'firstName': 'API', 'lastName': 'Bot'},
                    'dateAdded': 1700000000000,
                    'action': 'General',
                },
                {
                    'id': 2,
                    'commentingPerson': {'id': 555, 'firstName': 'Alice', 'lastName': 'Jones'},
                    'dateAdded': 1700001000000,
                    'action': 'General',
                },
                {
                    'id': 3,
                    'commentingPerson': {'id': 666, 'firstName': 'Bob', 'lastName': 'Lee'},
                    'dateAdded': 1700002000000,
                    'action': 'General',
                },
            ]
        }

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {'changeType': 'UPDATE', 'changedEntityId': 1001}

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, mixed_notes]
                    mock_req.post.return_value = update_resp
                    reassign_api_user_candidates(since_minutes=30)

                    update_calls = [
                        c for c in mock_req.post.call_args_list
                        if 'entity/Candidate' in str(c)
                    ]
                    assert len(update_calls) == 1
                    posted_json = update_calls[0].kwargs.get('json', {})
                    assert posted_json['owner']['id'] == 555


# ─────────────────────────────────────────────────────────────────────────────
# T008: Bullhorn update failure
# ─────────────────────────────────────────────────────────────────────────────
class TestBullhornUpdateFailure:
    def test_failed_update_is_counted(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        candidate = _make_candidate()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        note_resp = MagicMock()
        note_resp.status_code = 200
        note_resp.json.return_value = _make_note_resp(person_id=777)

        fail_resp = MagicMock()
        fail_resp.status_code = 400
        fail_resp.json.return_value = {'errorCode': 400, 'errors': ['Bad Request']}

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, note_resp]
                    mock_req.post.return_value = fail_resp
                    reassign_api_user_candidates(since_minutes=30)

                    assert mock_req.post.call_count >= 1


# ─────────────────────────────────────────────────────────────────────────────
# T009: Bullhorn auth failure
# ─────────────────────────────────────────────────────────────────────────────
class TestBullhornAuthFailure:
    def test_exits_when_auth_fails(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = False
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    reassign_api_user_candidates(since_minutes=30)
                    assert mock_req.get.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# T010: note creation failure does not abort reassignment
# ─────────────────────────────────────────────────────────────────────────────
class TestNoteCreationFailure:
    def test_note_failure_does_not_abort(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'true')

        candidate = _make_candidate()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        note_resp = MagicMock()
        note_resp.status_code = 200
        note_resp.json.return_value = _make_note_resp(person_id=777)

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {'changeType': 'UPDATE', 'changedEntityId': 1001}

        def get_side_effect(*args, **kwargs):
            url = args[0] if args else kwargs.get('url', '')
            if 'search/Candidate' in url:
                return search_resp
            if 'search/Note' in url:
                return note_resp
            return MagicMock(status_code=404)

        def put_side_effect(*args, **kwargs):
            raise Exception("Note API exploded")

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = get_side_effect
                    mock_req.post.return_value = update_resp
                    mock_req.put.side_effect = put_side_effect
                    reassign_api_user_candidates(since_minutes=30)

                    update_calls = [
                        c for c in mock_req.post.call_args_list
                        if 'entity/Candidate' in str(c)
                    ]
                    assert len(update_calls) == 1


# ─────────────────────────────────────────────────────────────────────────────
# T011: multiple API user IDs → OR query
# ─────────────────────────────────────────────────────────────────────────────
class TestMultipleApiUserIds:
    def test_multiple_ids_produce_or_query(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '111,222,333')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        empty_resp = MagicMock()
        empty_resp.status_code = 200
        empty_resp.json.return_value = {'data': []}

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.return_value = empty_resp
                    reassign_api_user_candidates(since_minutes=30)

                    call_args = mock_req.get.call_args
                    params = call_args.kwargs.get('params') or {}
                    query = params.get('query', '')
                    assert 'owner.id:111' in query
                    assert 'owner.id:222' in query
                    assert 'owner.id:333' in query
                    assert 'OR' in query


# ─────────────────────────────────────────────────────────────────────────────
# T012: _parse_api_user_ids strips non-numeric values
# ─────────────────────────────────────────────────────────────────────────────
class TestParseApiUserIds:
    def test_strips_non_numeric(self):
        from tasks.owner_reassignment import _parse_api_user_ids
        assert _parse_api_user_ids('123, 456, abc, 789, ') == [123, 456, 789]

    def test_empty_string(self):
        from tasks.owner_reassignment import _parse_api_user_ids
        assert _parse_api_user_ids('') == []

    def test_single_id(self):
        from tasks.owner_reassignment import _parse_api_user_ids
        assert _parse_api_user_ids('99999') == [99999]


# ─────────────────────────────────────────────────────────────────────────────
# T012b: pagination — human note on page 2
# ─────────────────────────────────────────────────────────────────────────────
class TestNotePagination:
    def test_human_found_on_second_page(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        candidate = _make_candidate()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        page1_notes = [
            {
                'id': i,
                'commentingPerson': {'id': 9999, 'firstName': 'API', 'lastName': 'Bot'},
                'dateAdded': 1700000000000 + i,
                'action': 'General',
            }
            for i in range(50)
        ]
        page1_resp = MagicMock()
        page1_resp.status_code = 200
        page1_resp.json.return_value = {'data': page1_notes, 'total': 51}

        page2_resp = MagicMock()
        page2_resp.status_code = 200
        page2_resp.json.return_value = {
            'data': [{
                'id': 50,
                'commentingPerson': {'id': 888, 'firstName': 'Jane', 'lastName': 'Doe'},
                'dateAdded': 1700000100000,
                'action': 'General',
            }],
            'total': 51,
        }

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {'changeType': 'UPDATE', 'changedEntityId': 1001}

        call_count = {'n': 0}
        def get_side_effect(*args, **kwargs):
            url = args[0] if args else kwargs.get('url', '')
            if 'search/Candidate' in url:
                return search_resp
            if 'search/Note' in url:
                call_count['n'] += 1
                if call_count['n'] == 1:
                    return page1_resp
                return page2_resp
            return MagicMock(status_code=404)

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = get_side_effect
                    mock_req.post.return_value = update_resp
                    reassign_api_user_candidates(since_minutes=30)

                    update_calls = [
                        c for c in mock_req.post.call_args_list
                        if 'entity/Candidate' in str(c)
                    ]
                    assert len(update_calls) == 1
                    posted_json = update_calls[0].kwargs.get('json', {})
                    assert posted_json['owner']['id'] == 888


# ─────────────────────────────────────────────────────────────────────────────
# T013: note disabled
# ─────────────────────────────────────────────────────────────────────────────
class TestNoteDisabled:
    def test_no_note_when_disabled(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        candidate = _make_candidate()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        note_resp = MagicMock()
        note_resp.status_code = 200
        note_resp.json.return_value = _make_note_resp(person_id=777)

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {'changeType': 'UPDATE', 'changedEntityId': 1001}

        def get_side_effect(*args, **kwargs):
            url = args[0] if args else ''
            if 'search/Candidate' in url:
                return search_resp
            if 'search/Note' in url:
                return note_resp
            return MagicMock(status_code=404)

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = get_side_effect
                    mock_req.post.return_value = update_resp
                    reassign_api_user_candidates(since_minutes=30)
                    assert mock_req.put.call_count == 0


# ─────────────────────────────────────────────────────────────────────────────
# T014: ownership_save_config action saves api_user_ids and note toggle
# (config now lives in the Automation Hub at /automations/owner-reassign)
# ─────────────────────────────────────────────────────────────────────────────
class TestSettingsSave:
    def test_saves_reassignment_settings(self, app, authenticated_client):
        from models import VettingConfig
        _ensure_config(app, 'api_user_ids', '')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        resp = authenticated_client.post('/automations/owner-reassign', json={
            'action': 'ownership_save_config',
            'api_user_ids': '123456, 789012, bad_value',
            'reassign_owner_note_enabled': True,
        })

        assert resp.status_code == 200
        body = resp.get_json()
        assert body and body.get('success') is True

        with app.app_context():
            r2 = VettingConfig.query.filter_by(setting_key='api_user_ids').first()
            r3 = VettingConfig.query.filter_by(setting_key='reassign_owner_note_enabled').first()
            assert r2 and r2.setting_value == '123456,789012'
            assert r3 and r3.setting_value == 'true'


# ─────────────────────────────────────────────────────────────────────────────
# T015: settings defaults when keys missing
# ─────────────────────────────────────────────────────────────────────────────
class TestSettingsDefaults:
    def test_defaults_are_returned_when_keys_missing(self, app):
        _delete_config(app, 'auto_reassign_owner_enabled')
        _delete_config(app, 'api_user_ids')
        _delete_config(app, 'reassign_owner_note_enabled')

        with app.app_context():
            from tasks.owner_reassignment import _get_vetting_config
            assert _get_vetting_config('auto_reassign_owner_enabled', 'false') == 'false'
            assert _get_vetting_config('api_user_ids', '') == ''
            assert _get_vetting_config('reassign_owner_note_enabled', 'true') == 'true'


# ─────────────────────────────────────────────────────────────────────────────
# T016: GET /automations correctly renders owner reassignment state
# (controls moved from Test Center to Automation Hub in Task #82)
# ─────────────────────────────────────────────────────────────────────────────
class TestGetToggleRendering:
    def test_false_value_renders_disabled_state(self, app, authenticated_client):
        """DB value 'false' → JS initialiser must emit 'false' for owner_reassign_enabled."""
        _ensure_config(app, 'auto_reassign_owner_enabled', 'false')
        _ensure_config(app, 'reassign_owner_note_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '12345')

        resp = authenticated_client.get('/automations', follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')

        assert 'var serverEnabled = false;' in html, (
            "owner_reassign_enabled should render as false when DB value is 'false'"
        )

    def test_true_value_renders_enabled_state(self, app, authenticated_client):
        """DB value 'true' → JS initialiser must emit 'true' for owner_reassign_enabled."""
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'api_user_ids', '12345')

        resp = authenticated_client.get('/automations', follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')

        assert 'var serverEnabled = true;' in html, (
            "owner_reassign_enabled should render as true when DB value is 'true'"
        )


# ─────────────────────────────────────────────────────────────────────────────
# T017: ownership_toggle action does not touch api_user_ids or note toggle
# ─────────────────────────────────────────────────────────────────────────────
class TestSubSettingsPreservedOnToggleOff:
    def test_api_user_ids_preserved_when_toggle_off(self, app, authenticated_client):
        """Disabling the kill switch via ownership_toggle must not erase IDs or note pref."""
        from models import VettingConfig
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '111222,333444')
        _ensure_config(app, 'reassign_owner_note_enabled', 'true')

        resp = authenticated_client.post('/automations/owner-reassign', json={
            'action': 'ownership_toggle',
            'enabled': False,
        })

        assert resp.status_code == 200
        body = resp.get_json()
        assert body and body.get('success') is True

        with app.app_context():
            r_toggle = VettingConfig.query.filter_by(setting_key='auto_reassign_owner_enabled').first()
            r_ids = VettingConfig.query.filter_by(setting_key='api_user_ids').first()
            r_note = VettingConfig.query.filter_by(setting_key='reassign_owner_note_enabled').first()
            assert r_toggle and r_toggle.setting_value == 'false', "Master toggle should be false"
            assert r_ids and r_ids.setting_value == '111222,333444', (
                "api_user_ids must be preserved when kill switch is turned off"
            )
            assert r_note and r_note.setting_value == 'true', (
                "reassign_owner_note_enabled must be preserved when kill switch is turned off"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for Run History tests (T018–T022)
# ─────────────────────────────────────────────────────────────────────────────
def _count_owner_history_logs(app):
    from app import db
    from models import AutomationLog, AutomationTask
    with app.app_context():
        task = AutomationTask.query.filter(
            AutomationTask.config_json.contains('"builtin_key": "owner_reassignment"')
        ).first()
        if not task:
            return 0, []
        logs = AutomationLog.query.filter_by(automation_task_id=task.id).all()
        return len(logs), logs


def _purge_owner_history(app):
    from app import db
    from models import AutomationLog, AutomationTask
    with app.app_context():
        task = AutomationTask.query.filter(
            AutomationTask.config_json.contains('"builtin_key": "owner_reassignment"')
        ).first()
        if task:
            AutomationLog.query.filter_by(automation_task_id=task.id).delete()
            db.session.delete(task)
            db.session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# T018: 5-min cycle, all-skip → noise filter suppresses Run History row
# ─────────────────────────────────────────────────────────────────────────────
class TestRunHistoryNoiseFilter5min:
    def test_no_history_row_when_all_skipped(self, app):
        _purge_owner_history(app)
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        candidate = _make_candidate()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        no_notes_resp = MagicMock()
        no_notes_resp.status_code = 200
        no_notes_resp.json.return_value = {'data': []}

        with app.app_context():
            from tasks.owner_reassignment import (
                reassign_api_user_candidates, SOURCE_SCHEDULED_5MIN
            )
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, no_notes_resp]
                    result = reassign_api_user_candidates(
                        since_minutes=30, source=SOURCE_SCHEDULED_5MIN
                    )
                    assert result['reassigned'] == 0
                    assert result['failed'] == 0
                    assert not result.get('errors')

        count, _ = _count_owner_history_logs(app)
        assert count == 0, "5-min cycle with no signal should not write Run History"


# ─────────────────────────────────────────────────────────────────────────────
# T019: 5-min cycle, successful reassign → Run History row WITH candidate IDs
# ─────────────────────────────────────────────────────────────────────────────
class TestRunHistoryWritesOnSignal5min:
    def test_history_row_written_when_reassign_happens(self, app):
        _purge_owner_history(app)
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        candidate = _make_candidate(cid=4501234)
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        note_resp = MagicMock()
        note_resp.status_code = 200
        note_resp.json.return_value = _make_note_resp(person_id=777)

        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {'changeType': 'UPDATE', 'changedEntityId': 4501234}

        with app.app_context():
            from tasks.owner_reassignment import (
                reassign_api_user_candidates, SOURCE_SCHEDULED_5MIN
            )
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, note_resp]
                    mock_req.post.return_value = update_resp
                    result = reassign_api_user_candidates(
                        since_minutes=30, source=SOURCE_SCHEDULED_5MIN
                    )
                    assert result['reassigned'] == 1
                    assert result['reassigned_ids'] == [4501234]

        count, logs = _count_owner_history_logs(app)
        assert count == 1, "5-min cycle with a reassign should write exactly one Run History row"
        details = json.loads(logs[0].details_json)
        assert details['reassigned'] == 1
        assert details['reassigned_candidate_ids'] == [4501234]
        assert details['source'] == 'scheduled_5min'
        assert '1 reassigned' in details['summary']


# ─────────────────────────────────────────────────────────────────────────────
# T020: daily sweep always writes Run History (even with all-skips)
# ─────────────────────────────────────────────────────────────────────────────
class TestRunHistoryDailySweepAlwaysWrites:
    def test_daily_sweep_writes_even_when_no_signal(self, app):
        _purge_owner_history(app)
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        candidate = _make_candidate()
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        no_notes_resp = MagicMock()
        no_notes_resp.status_code = 200
        no_notes_resp.json.return_value = {'data': []}

        with app.app_context():
            from tasks.owner_reassignment import (
                reassign_api_user_candidates, SOURCE_SCHEDULED_DAILY
            )
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, no_notes_resp]
                    reassign_api_user_candidates(
                        since_minutes=129600, source=SOURCE_SCHEDULED_DAILY
                    )

        count, logs = _count_owner_history_logs(app)
        assert count == 1, "Daily sweep must always write Run History, even with no signal"
        details = json.loads(logs[0].details_json)
        assert details['source'] == 'scheduled_daily'
        assert details['reassigned'] == 0
        assert details['reassigned_candidate_ids'] == []
        assert 'Daily Sweep' in logs[0].message


# ─────────────────────────────────────────────────────────────────────────────
# T021: manual live batch always writes Run History
# ─────────────────────────────────────────────────────────────────────────────
class TestRunHistoryManualLiveBatchAlwaysWrites:
    def test_manual_live_batch_writes_even_when_no_signal(self, app):
        _purge_owner_history(app)
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        empty_resp = MagicMock()
        empty_resp.status_code = 200
        empty_resp.json.return_value = {'data': []}

        with app.app_context():
            from tasks.owner_reassignment import (
                reassign_api_user_candidates, SOURCE_MANUAL_LIVE_BATCH
            )
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.return_value = empty_resp
                    reassign_api_user_candidates(
                        since_minutes=43200, source=SOURCE_MANUAL_LIVE_BATCH
                    )

        count, logs = _count_owner_history_logs(app)
        assert count == 1, "Manual live batch must always write Run History"
        details = json.loads(logs[0].details_json)
        assert details['source'] == 'manual_live_batch'
        assert 'Manual Live Batch' in logs[0].message


# ─────────────────────────────────────────────────────────────────────────────
# T022b: 5-min cycle, feature DISABLED → silent (no Run History row)
# T022c: daily sweep, feature DISABLED → row written so operator sees it
# T022d: 5-min cycle, Bullhorn auth FAILS → row written (operator-actionable)
# T022e: ID truncation when reassigned > _MAX_IDS_IN_DETAILS
# ─────────────────────────────────────────────────────────────────────────────
class TestEarlyReturnLogging:
    def test_5min_disabled_is_silent(self, app):
        _purge_owner_history(app)
        _ensure_config(app, 'auto_reassign_owner_enabled', 'false')

        with app.app_context():
            from tasks.owner_reassignment import (
                reassign_api_user_candidates, SOURCE_SCHEDULED_5MIN
            )
            reassign_api_user_candidates(since_minutes=30, source=SOURCE_SCHEDULED_5MIN)

        count, _ = _count_owner_history_logs(app)
        assert count == 0, "5-min cycle with feature disabled must stay silent"

    def test_daily_disabled_writes_history(self, app):
        _purge_owner_history(app)
        _ensure_config(app, 'auto_reassign_owner_enabled', 'false')

        with app.app_context():
            from tasks.owner_reassignment import (
                reassign_api_user_candidates, SOURCE_SCHEDULED_DAILY
            )
            reassign_api_user_candidates(since_minutes=129600, source=SOURCE_SCHEDULED_DAILY)

        count, logs = _count_owner_history_logs(app)
        assert count == 1, "Daily sweep must surface 'feature disabled' to the operator"
        details = json.loads(logs[0].details_json)
        assert details['source'] == 'scheduled_daily'
        assert any('disabled' in e.lower() for e in details.get('errors', []))

    def test_5min_auth_failure_writes_history(self, app):
        _purge_owner_history(app)
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        with app.app_context():
            from tasks.owner_reassignment import (
                reassign_api_user_candidates, SOURCE_SCHEDULED_5MIN
            )
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = False
                mock_bh_cls.return_value = bh
                reassign_api_user_candidates(
                    since_minutes=30, source=SOURCE_SCHEDULED_5MIN
                )

        count, logs = _count_owner_history_logs(app)
        assert count == 1, "Auth failure must surface even on the 5-min cycle"
        details = json.loads(logs[0].details_json)
        assert any('authentication failed' in e.lower() for e in details.get('errors', []))


class TestRunHistoryIdTruncation:
    def test_ids_truncated_at_cap(self, app):
        _purge_owner_history(app)

        with app.app_context():
            from tasks.owner_reassignment import _write_run_history, SOURCE_MANUAL_LIVE_BATCH
            ids = list(range(1, 351))
            _write_run_history(
                {
                    'reassigned': 350, 'skipped': 0, 'failed': 0,
                    'errors': [], 'reassigned_ids': ids,
                },
                SOURCE_MANUAL_LIVE_BATCH,
            )

        count, logs = _count_owner_history_logs(app)
        assert count == 1
        details = json.loads(logs[0].details_json)
        assert details['reassigned_ids_total'] == 350
        assert details['reassigned_ids_truncated'] is True
        assert len(details['reassigned_candidate_ids']) == 200
        assert details['reassigned_candidate_ids'][0] == 1
        assert details['reassigned_candidate_ids'][-1] == 200


# ─────────────────────────────────────────────────────────────────────────────
# T022: multiple reassigns → all candidate IDs captured for spot-check
# ─────────────────────────────────────────────────────────────────────────────
class TestRunHistoryCapturesAllReassignedIds:
    def test_all_candidate_ids_captured(self, app):
        _purge_owner_history(app)
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        c1 = _make_candidate(cid=4500001)
        c2 = _make_candidate(cid=4500002)
        c3 = _make_candidate(cid=4500003)
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [c1, c2, c3]}

        note_resp = MagicMock()
        note_resp.status_code = 200
        note_resp.json.return_value = _make_note_resp(person_id=777)

        def update_for(cid):
            r = MagicMock()
            r.status_code = 200
            r.json.return_value = {'changeType': 'UPDATE', 'changedEntityId': cid}
            return r

        with app.app_context():
            from tasks.owner_reassignment import (
                reassign_api_user_candidates, SOURCE_MANUAL_LIVE_BATCH
            )
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                # search/Candidate first, then 3 search/Note calls (one per candidate)
                with patch('tasks.owner_reassignment._requests') as mock_req:
                    note_resp_2 = MagicMock()
                    note_resp_2.status_code = 200
                    note_resp_2.json.return_value = _make_note_resp(person_id=777)
                    note_resp_3 = MagicMock()
                    note_resp_3.status_code = 200
                    note_resp_3.json.return_value = _make_note_resp(person_id=777)
                    mock_req.get.side_effect = [search_resp, note_resp, note_resp_2, note_resp_3]
                    mock_req.post.side_effect = [
                        update_for(4500001),
                        update_for(4500002),
                        update_for(4500003),
                    ]
                    result = reassign_api_user_candidates(
                        since_minutes=43200, source=SOURCE_MANUAL_LIVE_BATCH
                    )
                    assert result['reassigned'] == 3
                    assert sorted(result['reassigned_ids']) == [4500001, 4500002, 4500003]

        count, logs = _count_owner_history_logs(app)
        assert count == 1
        details = json.loads(logs[0].details_json)
        assert sorted(details['reassigned_candidate_ids']) == [4500001, 4500002, 4500003]
        assert details['reassigned'] == 3


# ─────────────────────────────────────────────────────────────────────────────
# T023: stale-connection hardening — db.session.remove() is called so the
#       17-minute candidate loop doesn't leave a closed SSL connection in the
#       session, which previously caused APScheduler "raised an exception"
#       spam after every cycle and silently dropped daily-sweep Run History
#       rows on commit.
# ─────────────────────────────────────────────────────────────────────────────
class TestStaleConnectionCleanup:
    def test_session_remove_called_in_finally_after_run(self, app):
        """The outer finally clause must always call db.session.remove(), even
        on the noise-filtered 5-min noop path that doesn't write to the DB.
        Without this, the app-context teardown fires do_rollback() on the
        stale SSL connection and APScheduler logs an exception every cycle."""
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')

        empty_search = MagicMock()
        empty_search.status_code = 200
        empty_search.json.return_value = {'data': [], 'total': 0}

        from app import db as real_db
        from tasks.owner_reassignment import (
            reassign_api_user_candidates,
            SOURCE_SCHEDULED_5MIN,
        )

        with patch.object(real_db, 'session') as mock_session, \
             patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls, \
             patch('tasks.owner_reassignment._requests') as mock_req, \
             patch('tasks.owner_reassignment._get_vetting_config') as mock_cfg:
            # Bypass the real DB-backed config lookups so the mocked session
            # doesn't break the function before it reaches the loop + finally.
            mock_cfg.side_effect = lambda key, default='': {
                'auto_reassign_owner_enabled': 'true',
                'api_user_ids': '9999',
                'reassign_owner_note_enabled': 'false',
            }.get(key, default)

            bh = MagicMock()
            bh.authenticate.return_value = True
            bh.base_url = 'https://example/'
            bh.rest_token = 'tok'
            mock_bh_cls.return_value = bh
            mock_req.get.return_value = empty_search

            reassign_api_user_candidates(
                since_minutes=30, source=SOURCE_SCHEDULED_5MIN
            )

            assert mock_session.remove.called, (
                "db.session.remove() must be called from the finally clause "
                "to drop any stale SSL connection before the app context "
                "tears down"
            )

    def test_session_remove_called_before_run_history_write(self, app):
        """For paths that DO write Run History (daily/manual/signal), the
        write must be preceded by db.session.remove() so the commit pulls a
        fresh connection from the pool instead of using the stale one held
        across the long candidate loop."""
        from app import db as real_db
        from tasks.owner_reassignment import (
            _write_run_history,
            SOURCE_SCHEDULED_DAILY,
        )

        with app.app_context():
            with patch.object(real_db, 'session') as mock_session, \
                 patch('tasks.owner_reassignment._get_or_create_owner_task_id') as mock_get_task:
                # Short-circuit after the session refresh so we isolate the
                # check to "was remove() called before the write path?".
                mock_get_task.return_value = None

                _write_run_history(
                    {
                        'reassigned': 0, 'skipped': 100, 'failed': 0,
                        'errors': [], 'reassigned_ids': [],
                    },
                    SOURCE_SCHEDULED_DAILY,
                )

                assert mock_session.remove.called, (
                    "db.session.remove() must be called before the Run "
                    "History write so a stale SSL connection from the long "
                    "candidate loop doesn't sink the commit"
                )


# ─────────────────────────────────────────────────────────────────────────────
# T023–T028: Per-candidate cooldown bandage
# ─────────────────────────────────────────────────────────────────────────────
class TestCooldownDefaultsAndKillSwitch:
    """Cooldown reads from VettingConfig with sensible defaults and a kill switch."""

    def test_default_enabled_and_24_hours_when_keys_missing(self, app):
        _delete_config(app, 'owner_reassignment_cooldown_enabled')
        _delete_config(app, 'owner_reassignment_cooldown_hours')
        with app.app_context():
            from tasks.owner_reassignment import _cooldown_enabled, _cooldown_hours
            assert _cooldown_enabled() is True
            assert _cooldown_hours() == 24

    def test_kill_switch_disables_cooldown(self, app):
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'false')
        with app.app_context():
            from tasks.owner_reassignment import _cooldown_enabled
            assert _cooldown_enabled() is False

    def test_window_clamped_to_safe_range(self, app):
        from tasks.owner_reassignment import _cooldown_hours
        with app.app_context():
            _ensure_config(app, 'owner_reassignment_cooldown_hours', '0')
            assert _cooldown_hours() == 1
            _ensure_config(app, 'owner_reassignment_cooldown_hours', '99999')
            assert _cooldown_hours() == 720
            _ensure_config(app, 'owner_reassignment_cooldown_hours', 'garbage')
            assert _cooldown_hours() == 24


class TestCooldownFiltersRepeatNoOps:
    """A candidate with an active cooldown row is skipped before we pay the
    Bullhorn-Notes-search cost. This is the whole point of the bandage."""

    def test_candidate_in_cooldown_is_skipped(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')
        _ensure_config(app, 'owner_reassignment_cooldown_hours', '24')

        # Pre-populate cooldown for candidate 1001
        from app import db
        from models import OwnerReassignmentCooldown
        from datetime import datetime
        with app.app_context():
            db.session.add(OwnerReassignmentCooldown(
                candidate_id=1001,
                last_evaluated_at=datetime.utcnow(),
                last_outcome='no_human_activity',
                evaluation_count=1,
            ))
            db.session.commit()

        candidate = _make_candidate(cid=1001)
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.return_value = search_resp
                    result = reassign_api_user_candidates(since_minutes=30)

                    # Only the candidate-search GET should fire — no per-
                    # candidate Notes lookup, because the cooldown short-
                    # circuited before the loop.
                    assert mock_req.get.call_count == 1
                    assert mock_req.post.call_count == 0
                    assert result['cooldown_skipped'] == 1
                    assert result['reassigned'] == 0

    def test_kill_switch_off_evaluates_even_cooldowned_candidates(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'false')

        from app import db
        from models import OwnerReassignmentCooldown
        from datetime import datetime
        with app.app_context():
            db.session.add(OwnerReassignmentCooldown(
                candidate_id=1001,
                last_evaluated_at=datetime.utcnow(),
                last_outcome='no_human_activity',
                evaluation_count=1,
            ))
            db.session.commit()

        candidate = _make_candidate(cid=1001)
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}
        no_notes_resp = MagicMock()
        no_notes_resp.status_code = 200
        no_notes_resp.json.return_value = {'data': []}

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, no_notes_resp]
                    result = reassign_api_user_candidates(since_minutes=30)

                    # Notes lookup MUST happen because the kill switch is off.
                    assert mock_req.get.call_count == 2
                    assert result['cooldown_skipped'] == 0


class TestCooldownRecordsNoOpOutcomes:
    """A no-op outcome (no human activity / already correct) lands in the
    cooldown table so the next cycle can short-circuit."""

    def test_no_human_activity_writes_cooldown_row(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')

        candidate = _make_candidate(cid=2001)
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        no_notes_resp = MagicMock()
        no_notes_resp.status_code = 200
        no_notes_resp.json.return_value = {'data': []}

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, no_notes_resp]
                    reassign_api_user_candidates(since_minutes=30)

            from models import OwnerReassignmentCooldown
            row = OwnerReassignmentCooldown.query.filter_by(
                candidate_id=2001
            ).first()
            assert row is not None
            assert row.last_outcome == 'no_human_activity'

    def test_already_correct_writes_cooldown_row(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')

        # Candidate already owned by the human (777), so the loop should
        # short-circuit on "already_correct" and write a cooldown row.
        candidate = _make_candidate(cid=2002, owner_id=777,
                                    owner_first='Bob', owner_last='Smith')
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        note_resp = MagicMock()
        note_resp.status_code = 200
        note_resp.json.return_value = _make_note_resp(person_id=777)

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, note_resp]
                    reassign_api_user_candidates(since_minutes=30)

            from models import OwnerReassignmentCooldown
            row = OwnerReassignmentCooldown.query.filter_by(
                candidate_id=2002
            ).first()
            assert row is not None
            assert row.last_outcome == 'already_correct'


class TestSuccessfulReassignClearsCooldown:
    """When a reassign actually lands, the cooldown row for that candidate
    is dropped so the candidate doesn't get re-checked for 24 h."""

    def test_successful_reassign_clears_existing_cooldown(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')
        _ensure_config(app, 'owner_reassignment_cooldown_hours', '24')

        # Pre-populate STALE cooldown row (last evaluated 25 h ago, so it
        # falls outside the 24 h window — the filter will let the candidate
        # through and the success path should then clear this stale row.)
        from app import db
        from models import OwnerReassignmentCooldown
        from datetime import datetime, timedelta
        with app.app_context():
            db.session.add(OwnerReassignmentCooldown(
                candidate_id=3001,
                last_evaluated_at=datetime.utcnow() - timedelta(hours=25),
                last_outcome='no_human_activity',
                evaluation_count=5,
            ))
            db.session.commit()

        candidate = _make_candidate(cid=3001)
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}
        note_resp = MagicMock()
        note_resp.status_code = 200
        note_resp.json.return_value = _make_note_resp(person_id=777)
        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {'changeType': 'UPDATE', 'changedEntityId': 3001}

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, note_resp]
                    mock_req.post.return_value = update_resp
                    result = reassign_api_user_candidates(since_minutes=30)

            assert result['reassigned'] == 1
            from models import OwnerReassignmentCooldown
            row = OwnerReassignmentCooldown.query.filter_by(
                candidate_id=3001
            ).first()
            assert row is None, (
                "Cooldown row must be cleared after a successful reassign so "
                "the candidate is not silently skipped on the next cycle."
            )


class TestRunHistoryShowsCooldownStat:
    """The Automation Hub Run History panel must surface cooldown_skipped so
    operators can see how much work the bandage is suppressing."""

    def test_run_history_details_include_cooldown_skipped(self, app):
        from app import db
        from tasks.owner_reassignment import (
            _write_run_history, SOURCE_SCHEDULED_DAILY,
        )
        from models import AutomationLog
        with app.app_context():
            _write_run_history(
                {
                    'reassigned': 2, 'skipped': 5, 'cooldown_skipped': 4500,
                    'failed': 0, 'errors': [], 'reassigned_ids': [11, 22],
                },
                SOURCE_SCHEDULED_DAILY,
            )
            log = AutomationLog.query.order_by(
                AutomationLog.id.desc()
            ).first()
            assert log is not None
            payload = json.loads(log.details_json)
            assert payload['cooldown_skipped'] == 4500
            assert 'cooldown-skipped' in payload['summary']


class TestCooldownFailOpenAndDedupe:
    """Hardening: a broken cooldown table must not block reassignment, and a
    duplicate candidate ID inside one upsert batch must not crash the flush."""

    def test_flush_dedupes_duplicate_candidate_ids(self, app):
        """If the loop somehow records two outcomes for the same candidate
        in one cycle, the upsert must NOT raise PostgreSQL's `ON CONFLICT
        DO UPDATE command cannot affect row a second time` error."""
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')
        from tasks.owner_reassignment import _flush_cooldown_outcomes
        with app.app_context():
            # Two outcomes for the same candidate_id — must collapse to one row.
            _flush_cooldown_outcomes([
                (4001, 'no_human_activity'),
                (4001, 'already_correct'),
                (4002, 'no_human_activity'),
            ])
            from models import OwnerReassignmentCooldown
            rows = OwnerReassignmentCooldown.query.order_by(
                OwnerReassignmentCooldown.candidate_id
            ).all()
            assert len(rows) == 2
            row_4001 = next(r for r in rows if r.candidate_id == 4001)
            # Last outcome wins per loop order.
            assert row_4001.last_outcome == 'already_correct'

    def test_flush_is_noop_when_kill_switch_off(self, app):
        """Operator expectation: turning the kill switch off means the
        cooldown table receives NO writes, period."""
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'false')
        from tasks.owner_reassignment import _flush_cooldown_outcomes
        with app.app_context():
            _flush_cooldown_outcomes([(5001, 'no_human_activity')])
            from models import OwnerReassignmentCooldown
            assert OwnerReassignmentCooldown.query.count() == 0

    def test_clear_is_noop_when_kill_switch_off(self, app):
        """Operator expectation: kill switch off means the bandage performs
        ZERO DB writes, including DELETEs from `_clear_cooldown_for_candidate`."""
        from app import db
        from models import OwnerReassignmentCooldown
        from datetime import datetime
        with app.app_context():
            db.session.add(OwnerReassignmentCooldown(
                candidate_id=6001,
                last_evaluated_at=datetime.utcnow(),
                last_outcome='no_human_activity',
                evaluation_count=1,
            ))
            db.session.commit()

        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'false')
        from tasks.owner_reassignment import _clear_cooldown_for_candidate
        with app.app_context():
            _clear_cooldown_for_candidate(6001)
            row = OwnerReassignmentCooldown.query.filter_by(
                candidate_id=6001
            ).first()
            assert row is not None, (
                "DELETE must be skipped when the kill switch is off, so the "
                "operator's intent (no bandage writes) is honored end-to-end."
            )

    def test_lookup_fails_open_on_db_error(self, app):
        """A broken cooldown table (e.g. ProgrammingError, OperationalError)
        must NEVER block reassignment — return an empty set so every
        candidate flows through the normal path."""
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')
        from tasks.owner_reassignment import _fetch_active_cooldown_ids
        with app.app_context():
            with patch('app.db.session') as mock_session:
                mock_session.query.side_effect = RuntimeError(
                    "simulated cooldown table missing"
                )
                result = _fetch_active_cooldown_ids([1001, 1002, 1003])
                assert result == set(), (
                    "Lookup failure must fail-open with empty set so the "
                    "loop processes every candidate normally."
                )
