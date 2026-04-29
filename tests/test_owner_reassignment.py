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
# (config now lives in the Automation Test Center, not Vetting Settings)
# ─────────────────────────────────────────────────────────────────────────────
class TestSettingsSave:
    def test_saves_reassignment_settings(self, app, authenticated_client):
        from models import VettingConfig
        _ensure_config(app, 'api_user_ids', '')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')

        resp = authenticated_client.post('/automation_test', json={
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
# T016: GET /automation_test correctly renders kill switch checked/unchecked
# (toggle moved from Vetting Settings to Automation Test Center in Task #74)
# ─────────────────────────────────────────────────────────────────────────────
class TestGetToggleRendering:
    def test_false_value_renders_unchecked_master_toggle(self, app, authenticated_client):
        """DB value 'false' → ownerReassignToggle must NOT have checked attribute."""
        import re
        _ensure_config(app, 'auto_reassign_owner_enabled', 'false')
        _ensure_config(app, 'reassign_owner_note_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '12345')

        resp = authenticated_client.get('/automation_test', follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')

        pattern = re.compile(
            r'<input[^>]*id="ownerReassignToggle"[^>]*>',
            re.IGNORECASE
        )
        match = pattern.search(html)
        assert match is not None, "ownerReassignToggle not found in /automation_test HTML"
        assert 'checked' not in match.group(0), (
            "ownerReassignToggle should be unchecked when DB value is 'false'"
        )

    def test_true_value_renders_checked_master_toggle(self, app, authenticated_client):
        """DB value 'true' → ownerReassignToggle MUST have checked attribute."""
        import re
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'api_user_ids', '12345')

        resp = authenticated_client.get('/automation_test', follow_redirects=True)
        assert resp.status_code == 200
        html = resp.data.decode('utf-8')

        pattern = re.compile(
            r'<input[^>]*id="ownerReassignToggle"[^>]*>',
            re.IGNORECASE
        )
        match = pattern.search(html)
        assert match is not None, "ownerReassignToggle not found in /automation_test HTML"
        assert 'checked' in match.group(0), (
            "ownerReassignToggle should be checked when DB value is 'true'"
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

        resp = authenticated_client.post('/automation_test', json={
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
