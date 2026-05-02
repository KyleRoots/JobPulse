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
                    owner_id=9999, owner_first='API', owner_last='Bot',
                    date_last_modified=None):
    """
    Build a fake Bullhorn candidate dict.

    `date_last_modified` is the ms-epoch value Bullhorn returns for the
    `dateLastModified` field. Pass an int to test the cooldown-invalidation
    path; leave None to omit the field (legacy behavior).
    """
    cand = {
        'id': cid,
        'firstName': first,
        'lastName': last,
        'owner': {'id': owner_id, 'firstName': owner_first, 'lastName': owner_last},
    }
    if date_last_modified is not None:
        cand['dateLastModified'] = date_last_modified
    return cand


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
        # Disable heartbeat so this test isolates pure noise-filter behavior.
        # Heartbeat behavior is covered by TestRunHistoryHeartbeat below.
        _ensure_config(app, 'owner_reassignment_heartbeat_hours', '0')

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
# T019b: heartbeat — when noise filter would suppress, periodic heartbeat row
# is written so operators always see proof of life
# ─────────────────────────────────────────────────────────────────────────────
class TestRunHistoryHeartbeat:
    def _setup(self, app, heartbeat_hours='1'):
        _purge_owner_history(app)
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'owner_reassignment_heartbeat_hours', heartbeat_hours)

    def _run_no_signal_cycle(self, app):
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
                    return reassign_api_user_candidates(
                        since_minutes=30, source=SOURCE_SCHEDULED_5MIN
                    )

    def test_heartbeat_fires_on_first_no_signal_cycle(self, app):
        """No prior history → heartbeat is due → row should be written."""
        self._setup(app, heartbeat_hours='1')
        result = self._run_no_signal_cycle(app)
        assert result['reassigned'] == 0

        count, logs = _count_owner_history_logs(app)
        assert count == 1, "First no-signal cycle should write a heartbeat row"
        details = json.loads(logs[0].details_json)
        assert details.get('is_heartbeat') is True
        assert details.get('heartbeat_hours') == 1
        assert 'heartbeat' in (logs[0].message or '').lower()
        assert 'heartbeat' in details.get('summary', '').lower()

    def test_heartbeat_suppressed_when_within_window(self, app):
        """Two back-to-back no-signal cycles → only the first writes a heartbeat."""
        self._setup(app, heartbeat_hours='1')
        self._run_no_signal_cycle(app)
        count_after_first, _ = _count_owner_history_logs(app)
        assert count_after_first == 1

        self._run_no_signal_cycle(app)
        count_after_second, _ = _count_owner_history_logs(app)
        assert count_after_second == 1, (
            "Second cycle within the heartbeat window should NOT write another row"
        )

    def test_heartbeat_disabled_when_hours_is_zero(self, app):
        """heartbeat_hours='0' → kill switch → no row written even on no-signal."""
        self._setup(app, heartbeat_hours='0')
        self._run_no_signal_cycle(app)
        count, _ = _count_owner_history_logs(app)
        assert count == 0, (
            "heartbeat_hours=0 should disable heartbeat; no Run History row expected"
        )

    def test_heartbeat_clamps_garbage_to_default(self, app):
        """Non-numeric heartbeat_hours → defaults to 1 → heartbeat fires."""
        self._setup(app, heartbeat_hours='not-a-number')
        result = self._run_no_signal_cycle(app)
        assert result['reassigned'] == 0
        count, logs = _count_owner_history_logs(app)
        assert count == 1
        details = json.loads(logs[0].details_json)
        assert details.get('heartbeat_hours') == 1

    def test_heartbeat_clamps_negative_to_zero(self, app):
        """heartbeat_hours='-5' → clamps to 0 → heartbeat disabled."""
        self._setup(app, heartbeat_hours='-5')
        self._run_no_signal_cycle(app)
        count, _ = _count_owner_history_logs(app)
        assert count == 0, "Negative heartbeat_hours should clamp to 0 (disabled)"

    def test_heartbeat_clamps_excessive_to_24(self, app):
        """heartbeat_hours='999' → clamps to 24 → heartbeat still fires on first cycle."""
        self._setup(app, heartbeat_hours='999')
        self._run_no_signal_cycle(app)
        count, logs = _count_owner_history_logs(app)
        assert count == 1
        details = json.loads(logs[0].details_json)
        assert details.get('heartbeat_hours') == 24, (
            "Excessive heartbeat_hours should clamp to 24"
        )


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

                    # Exactly TWO GETs should fire:
                    #   1. Candidate search (always).
                    #   2. Single-shot bug-#4 note-buster batch search
                    #      (added May 2026 — closes the note-add blind
                    #      spot in `_candidate_modified_after`).
                    # Critically, NO per-candidate
                    # `_find_first_human_interactor` GET fires, because
                    # the cooldown still short-circuits the per-candidate
                    # loop. Mock returns no real notes, so no buster IDs,
                    # so candidate 1001 stays cooldown-skipped.
                    assert mock_req.get.call_count == 2, (
                        f"Expected 2 GETs (candidate search + 1 note-"
                        f"buster batch); got {mock_req.get.call_count}"
                    )
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


# ─────────────────────────────────────────────────────────────────────────────
# T029–T031: Run History badge classification (Apr 2026 fix)
#
# Operator UX: the Automation Hub badge for each run must reflect actual
# severity. The pre-fix logic painted RED for transient Bullhorn 5xx during
# otherwise-idle cycles (~4/day false alarms) while only painting AMBER for
# real candidate-level failures. The corrected mapping is:
#   - failed > 0  → 'error'   (real candidate-level failures)
#   - errors only → 'warning' (transient upstream issues, e.g. Bullhorn 504)
#   - clean run   → 'success'
# ─────────────────────────────────────────────────────────────────────────────
class TestRunHistoryBadgeClassification:
    """The badge surfaced in the Automation Hub must distinguish transient
    upstream errors from real candidate-level failures, so operators trust
    the colors instead of treating every red as wallpaper."""

    def test_status_warning_when_transient_errors_only(self, app):
        """A Bullhorn HTTP 504 on a cycle with no candidates touched should
        paint AMBER (warning), not RED (error)."""
        from tasks.owner_reassignment import (
            _write_run_history, SOURCE_MANUAL_LIVE_BATCH,
        )
        from models import AutomationLog
        with app.app_context():
            _write_run_history(
                {
                    'reassigned': 0, 'skipped': 0, 'cooldown_skipped': 0,
                    'failed': 0,
                    'errors': ['Candidate search failed: HTTP 504'],
                    'reassigned_ids': [],
                },
                SOURCE_MANUAL_LIVE_BATCH,
            )
            log = AutomationLog.query.order_by(
                AutomationLog.id.desc()
            ).first()
            assert log is not None
            assert log.status == 'warning', (
                f"Transient upstream error with zero candidate-level harm "
                f"must classify as 'warning' (amber), not 'error' (red). "
                f"Got: {log.status!r}"
            )

    def test_status_error_when_real_candidate_failures(self, app):
        """A run that actually failed to reassign one or more candidates
        must paint RED (error) regardless of whether errors[] is set."""
        from tasks.owner_reassignment import (
            _write_run_history, SOURCE_MANUAL_LIVE_BATCH,
        )
        from models import AutomationLog
        with app.app_context():
            _write_run_history(
                {
                    'reassigned': 3, 'skipped': 1, 'cooldown_skipped': 0,
                    'failed': 2,
                    'errors': [],
                    'reassigned_ids': [101, 102, 103],
                },
                SOURCE_MANUAL_LIVE_BATCH,
            )
            log = AutomationLog.query.order_by(
                AutomationLog.id.desc()
            ).first()
            assert log is not None
            assert log.status == 'error', (
                f"Real candidate-level failures (failed > 0) must classify "
                f"as 'error' (red) so they stand out. Got: {log.status!r}"
            )

    def test_status_success_when_clean_run(self, app):
        """A run with no failures and no errors must remain GREEN."""
        from tasks.owner_reassignment import (
            _write_run_history, SOURCE_MANUAL_LIVE_BATCH,
        )
        from models import AutomationLog
        with app.app_context():
            _write_run_history(
                {
                    'reassigned': 5, 'skipped': 2, 'cooldown_skipped': 100,
                    'failed': 0,
                    'errors': [],
                    'reassigned_ids': [201, 202, 203, 204, 205],
                },
                SOURCE_MANUAL_LIVE_BATCH,
            )
            log = AutomationLog.query.order_by(
                AutomationLog.id.desc()
            ).first()
            assert log is not None
            assert log.status == 'success', (
                f"Clean run (no failures, no errors) must classify as "
                f"'success' (green). Got: {log.status!r}"
            )

    def test_status_error_when_failures_and_errors_both_present(self, app):
        """If a run has BOTH transient errors AND real candidate failures,
        the real failures take precedence — paint RED."""
        from tasks.owner_reassignment import (
            _write_run_history, SOURCE_MANUAL_LIVE_BATCH,
        )
        from models import AutomationLog
        with app.app_context():
            _write_run_history(
                {
                    'reassigned': 1, 'skipped': 0, 'cooldown_skipped': 0,
                    'failed': 4,
                    'errors': ['Candidate search failed: HTTP 504'],
                    'reassigned_ids': [301],
                },
                SOURCE_MANUAL_LIVE_BATCH,
            )
            log = AutomationLog.query.order_by(
                AutomationLog.id.desc()
            ).first()
            assert log is not None
            assert log.status == 'error', (
                f"Real failures take precedence over transient errors — "
                f"failed > 0 always classifies as 'error'. Got: {log.status!r}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# T023: cooldown invalidation via Bullhorn dateLastModified
#
# Bug scenario (May 2026 production): a candidate evaluated at 03:19 UTC
# landed in cooldown with `last_outcome='no_human_activity'`. A recruiter
# left a note on the candidate at 03:30 UTC — well within the 24h cooldown
# window — but the next 5-min cycle still skipped them, because the cooldown
# filter was a pure timestamp check that ignored fresh Bullhorn activity.
# Result: ownership stayed with the API account for ~24h until the cooldown
# naturally expired.
#
# Fix under test: when Bullhorn's `dateLastModified` is strictly newer than
# the cooldown row's `last_evaluated_at`, the cooldown is BUSTED for that
# cycle and the candidate is re-evaluated immediately.
# ─────────────────────────────────────────────────────────────────────────────
class TestCooldownInvalidationByDateLastModified:
    """A candidate with an active cooldown row but a newer `dateLastModified`
    in Bullhorn must be re-evaluated this cycle (cooldown busted)."""

    def test_cooldown_busted_when_candidate_modified_after_evaluation(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')
        _ensure_config(app, 'owner_reassignment_cooldown_hours', '24')

        # Cooldown was set 30 minutes ago; candidate was modified 5 min ago.
        from app import db
        from models import OwnerReassignmentCooldown
        from datetime import datetime, timedelta
        evaluated_at = datetime.utcnow() - timedelta(minutes=30)
        modified_at = datetime.utcnow() - timedelta(minutes=5)
        modified_ms = int(modified_at.timestamp() * 1000)

        with app.app_context():
            db.session.add(OwnerReassignmentCooldown(
                candidate_id=4001,
                last_evaluated_at=evaluated_at,
                last_outcome='no_human_activity',
                evaluation_count=1,
            ))
            db.session.commit()

        candidate = _make_candidate(cid=4001, date_last_modified=modified_ms)
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {'data': [candidate]}

        # Bullhorn now returns a human note from the recruiter.
        note_resp = MagicMock()
        note_resp.status_code = 200
        note_resp.json.return_value = _make_note_resp(person_id=777)

        # Successful owner update.
        update_resp = MagicMock()
        update_resp.status_code = 200
        update_resp.json.return_value = {
            'changeType': 'UPDATE', 'changedEntityId': 4001,
        }

        with app.app_context():
            from tasks.owner_reassignment import reassign_api_user_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                # Bug-#4 note-buster batch search now fires unconditionally
                # whenever cooldown_state is non-empty. Returns no notes
                # so it adds nothing to the buster set — `dateLastModified`
                # is what busts this candidate.
                note_buster_resp = MagicMock()
                note_buster_resp.status_code = 200
                note_buster_resp.json.return_value = {'data': [], 'total': 0}

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [
                        search_resp, note_buster_resp, note_resp,
                    ]
                    mock_req.post.return_value = update_resp
                    result = reassign_api_user_candidates(since_minutes=30)

                    # Cooldown was busted by dateLastModified, so the
                    # candidate IS evaluated and the recruiter note IS
                    # fetched — proving the filter let it through. Three
                    # GETs total: candidate search + bug-#4 note-buster
                    # batch + per-candidate Notes lookup.
                    assert mock_req.get.call_count == 3, (
                        "Notes lookup must fire when cooldown is busted by "
                        "dateLastModified; got call_count="
                        f"{mock_req.get.call_count}"
                    )
                    assert result['cooldown_skipped'] == 0
                    assert result['reassigned'] == 1
                    assert 4001 in result['reassigned_ids']

    def test_cooldown_holds_when_candidate_not_modified_after_evaluation(self, app):
        """If `dateLastModified` is older than (or equal to) `last_evaluated_at`,
        the cooldown stands and the Notes lookup is short-circuited."""
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')
        _ensure_config(app, 'owner_reassignment_cooldown_hours', '24')

        from app import db
        from models import OwnerReassignmentCooldown
        from datetime import datetime, timedelta
        evaluated_at = datetime.utcnow() - timedelta(minutes=5)
        # Candidate last modified 30 min ago — BEFORE the cooldown fired.
        modified_at = datetime.utcnow() - timedelta(minutes=30)
        modified_ms = int(modified_at.timestamp() * 1000)

        with app.app_context():
            db.session.add(OwnerReassignmentCooldown(
                candidate_id=4002,
                last_evaluated_at=evaluated_at,
                last_outcome='no_human_activity',
                evaluation_count=1,
            ))
            db.session.commit()

        candidate = _make_candidate(cid=4002, date_last_modified=modified_ms)
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

                    # Two GETs: candidate search + bug-#4 note-buster
                    # batch. The per-candidate
                    # `_find_first_human_interactor` GET MUST NOT fire —
                    # cooldown still holds because dateLastModified is
                    # stale and the (mocked) note search returns no real
                    # notes for non-API authors.
                    assert mock_req.get.call_count == 2, (
                        f"Expected 2 GETs (candidate search + note-buster "
                        f"batch); got {mock_req.get.call_count}"
                    )
                    assert mock_req.post.call_count == 0
                    assert result['cooldown_skipped'] == 1
                    assert result['reassigned'] == 0

    def test_cooldown_holds_when_dateLastModified_missing(self, app):
        """Defensive case: a candidate without a `dateLastModified` field
        defaults to "no new activity" so the cooldown stays in force.
        Preserves legacy behavior — cooldown must never be busted by a
        missing/malformed Bullhorn field."""
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')

        from app import db
        from models import OwnerReassignmentCooldown
        from datetime import datetime
        with app.app_context():
            db.session.add(OwnerReassignmentCooldown(
                candidate_id=4003,
                last_evaluated_at=datetime.utcnow(),
                last_outcome='no_human_activity',
                evaluation_count=1,
            ))
            db.session.commit()

        # Note: no `date_last_modified` kwarg → field omitted entirely.
        candidate = _make_candidate(cid=4003)
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

                    # Two GETs: candidate search + bug-#4 note-buster
                    # batch. The per-candidate Notes lookup MUST NOT fire
                    # — cooldown stands because dateLastModified is
                    # missing AND no recent non-API note exists for
                    # candidate 4003 in the mock note-buster response.
                    assert mock_req.get.call_count == 2, (
                        f"Expected 2 GETs (candidate search + note-buster "
                        f"batch); got {mock_req.get.call_count}"
                    )
                    assert result['cooldown_skipped'] == 1


class TestCooldownStateHelper:
    """Direct tests for the new `_fetch_cooldown_state` helper that returns
    a dict instead of a set, enabling per-candidate dateLastModified checks."""

    def test_returns_empty_dict_when_cooldown_disabled(self, app):
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'false')
        with app.app_context():
            from tasks.owner_reassignment import _fetch_cooldown_state
            assert _fetch_cooldown_state([1, 2, 3]) == {}

    def test_returns_dict_keyed_by_candidate_id(self, app):
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')
        _ensure_config(app, 'owner_reassignment_cooldown_hours', '24')

        from app import db
        from models import OwnerReassignmentCooldown
        from datetime import datetime, timedelta
        recent = datetime.utcnow() - timedelta(minutes=10)
        stale = datetime.utcnow() - timedelta(hours=48)

        with app.app_context():
            db.session.add(OwnerReassignmentCooldown(
                candidate_id=5001,
                last_evaluated_at=recent,
                last_outcome='no_human_activity',
                evaluation_count=1,
            ))
            db.session.add(OwnerReassignmentCooldown(
                candidate_id=5002,
                last_evaluated_at=stale,
                last_outcome='no_human_activity',
                evaluation_count=1,
            ))
            db.session.commit()

            from tasks.owner_reassignment import _fetch_cooldown_state
            state = _fetch_cooldown_state([5001, 5002, 5003])
            # 5001 in window → present; 5002 stale → absent; 5003 no row.
            assert 5001 in state
            assert 5002 not in state
            assert 5003 not in state
            assert state[5001] == recent


# ─────────────────────────────────────────────────────────────────────────────
# T024: preview_reassign_candidates cooldown payload — regression guard
#
# Catches the rename hazard in the preview path: when `_fetch_cooldown_state`
# replaced `_fetch_active_cooldown_ids`, the result dict was renamed from
# `cooldown_active` → `cooldown_state` but a downstream `len(cooldown_active)`
# reference would silently NameError on every successful preview call.
# This test exercises the full preview path so any future rename of the
# state dict variable will fail loudly here.
# ─────────────────────────────────────────────────────────────────────────────
class TestPreviewCooldownPayload:
    def test_preview_returns_cooldown_active_count(self, app):
        _ensure_config(app, 'auto_reassign_owner_enabled', 'true')
        _ensure_config(app, 'api_user_ids', '9999')
        _ensure_config(app, 'reassign_owner_note_enabled', 'false')
        _ensure_config(app, 'owner_reassignment_cooldown_enabled', 'true')
        _ensure_config(app, 'owner_reassignment_cooldown_hours', '24')

        # Pre-populate cooldown for one of the two preview candidates.
        from app import db
        from models import OwnerReassignmentCooldown
        from datetime import datetime
        with app.app_context():
            db.session.add(OwnerReassignmentCooldown(
                candidate_id=7001,
                last_evaluated_at=datetime.utcnow(),
                last_outcome='no_human_activity',
                evaluation_count=1,
            ))
            db.session.commit()

        cand_in_cooldown = _make_candidate(cid=7001)
        cand_fresh = _make_candidate(cid=7002)
        search_resp = MagicMock()
        search_resp.status_code = 200
        search_resp.json.return_value = {
            'data': [cand_in_cooldown, cand_fresh],
            'total': 2,
        }
        # Note search for the fresh candidate returns no human activity.
        no_notes_resp = MagicMock()
        no_notes_resp.status_code = 200
        no_notes_resp.json.return_value = {'data': []}

        with app.app_context():
            from tasks.owner_reassignment import preview_reassign_candidates
            with patch('tasks.owner_reassignment.BullhornService') as mock_bh_cls:
                bh = MagicMock()
                bh.authenticate.return_value = True
                bh.base_url = 'https://rest.bullhorn.com/'
                bh.rest_token = 'test-token'
                mock_bh_cls.return_value = bh

                with patch('tasks.owner_reassignment._requests') as mock_req:
                    mock_req.get.side_effect = [search_resp, no_notes_resp]
                    out = preview_reassign_candidates(limit=5)

                    # Smoke: payload contract must include the cooldown
                    # summary keys (regression guard for the rename hazard).
                    assert 'cooldown_active_in_sample' in out, (
                        f"Preview payload missing 'cooldown_active_in_sample' "
                        f"key. Got: {list(out.keys())}"
                    )
                    assert out['cooldown_active_in_sample'] == 1
                    assert out['cooldown_enabled'] is True
                    assert out['cooldown_window_hours'] == 24

                    # Per-candidate flag must mark the cooldowned one.
                    by_id = {c['candidate_id']: c for c in out['candidates']}
                    assert by_id[7001]['in_cooldown'] is True
                    assert by_id[7001]['skip_reason'].startswith('In cooldown')
                    assert by_id[7002]['in_cooldown'] is False


# ─────────────────────────────────────────────────────────────────────────────
# T025: _candidate_modified_after boundary cases
#
# The cooldown invalidation gate hinges entirely on this helper. Lock down
# the boundary semantics with a focused unit test so a future "let's loosen
# the comparison to >=" change can't silently regress correctness.
# ─────────────────────────────────────────────────────────────────────────────
class TestCandidateModifiedAfter:
    def test_returns_false_when_last_evaluated_is_none(self, app):
        from tasks.owner_reassignment import _candidate_modified_after
        # No cooldown row → caller should not even ask, but defensive return.
        assert _candidate_modified_after(
            {'dateLastModified': 1700000000000}, None
        ) is False

    def test_returns_false_when_field_missing(self, app):
        from tasks.owner_reassignment import _candidate_modified_after
        from datetime import datetime
        assert _candidate_modified_after({}, datetime.utcnow()) is False
        assert _candidate_modified_after(
            {'dateLastModified': None}, datetime.utcnow()
        ) is False

    def test_returns_false_for_malformed_timestamp(self, app):
        from tasks.owner_reassignment import _candidate_modified_after
        from datetime import datetime
        now = datetime.utcnow()
        for bad in ('not-a-number', '', [], {}, object()):
            assert _candidate_modified_after(
                {'dateLastModified': bad}, now
            ) is False, f"Malformed value {bad!r} must NOT bust cooldown"

    def test_strictly_greater_than_required(self, app):
        """Equal-second timestamps must NOT bust cooldown — only a strictly
        newer dateLastModified counts as fresh activity. This guards against
        a future change loosening the comparison to >= which would
        re-introduce the original infinite-re-screen behavior."""
        from tasks.owner_reassignment import _candidate_modified_after
        from datetime import datetime
        ts = 1700000000000
        evaluated_at = datetime.utcfromtimestamp(ts / 1000.0)
        # Equal → not modified after.
        assert _candidate_modified_after(
            {'dateLastModified': ts}, evaluated_at
        ) is False
        # 1 ms newer → busted.
        assert _candidate_modified_after(
            {'dateLastModified': ts + 1}, evaluated_at
        ) is True
        # 1 ms older → still in cooldown.
        assert _candidate_modified_after(
            {'dateLastModified': ts - 1}, evaluated_at
        ) is False

    def test_string_ms_epoch_accepted(self, app):
        """Bullhorn occasionally returns numeric fields as strings — int()
        coercion must accept them so the cooldown gate doesn't false-hold."""
        from tasks.owner_reassignment import _candidate_modified_after
        from datetime import datetime, timedelta
        evaluated_at = datetime.utcnow() - timedelta(hours=1)
        modified_ms = int(datetime.utcnow().timestamp() * 1000)
        assert _candidate_modified_after(
            {'dateLastModified': str(modified_ms)}, evaluated_at
        ) is True


# ─────────────────────────────────────────────────────────────────────────────
# T026: _find_first_human_interactor query syntax — regression guard
#
# This is the latent bug that caused ZERO reassignments across 22,270
# evaluated candidates: the function queried Bullhorn with `candidates.id:X`
# (plural to-many association) which returns no rows for any candidate.
# The correct field is `personReference.id:X`, mirroring the working query
# in `screening/dedup.py::_has_recent_recruiter_activity`.
#
# This test pins down the exact query string so a future "let's clean this
# up to use candidates.id" refactor will fail loudly here.
# ─────────────────────────────────────────────────────────────────────────────
class TestFindFirstHumanInteractorQuerySyntax:
    def test_uses_personReference_id_not_candidates_id(self, app):
        from tasks.owner_reassignment import _find_first_human_interactor

        captured_params = {}

        def fake_get(url, headers=None, params=None, timeout=None):
            captured_params['url'] = url
            captured_params['params'] = dict(params or {})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {'data': [], 'total': 0}
            return mock_resp

        with patch('tasks.owner_reassignment._requests') as mock_req:
            mock_req.get.side_effect = fake_get
            with app.app_context():
                result = _find_first_human_interactor(
                    base_url='https://rest.bullhorn.com/',
                    headers={'BhRestToken': 'tok'},
                    candidate_id=4656965,
                    api_user_ids=[1147490, 4582015],
                )

        assert result == (None, None, None)
        assert captured_params['url'] == 'https://rest.bullhorn.com/search/Note'
        query = captured_params['params'].get('query', '')
        assert 'personReference.id:4656965' in query, (
            f"Note search must use `personReference.id` (matches working "
            f"screening/dedup.py syntax). Got query: {query!r}"
        )
        assert 'candidates.id:' not in query, (
            f"Note search must NOT use `candidates.id` — that field returns "
            f"zero notes for every candidate in production. Got: {query!r}"
        )

    def test_finds_human_interactor_when_present(self, app):
        """End-to-end: returns the first non-API author found in the notes."""
        from tasks.owner_reassignment import _find_first_human_interactor

        notes_page = {
            'data': [
                {  # API-authored — must be skipped
                    'id': 1,
                    'commentingPerson': {'id': 1147490, 'firstName': 'Myticas', 'lastName': 'API User'},
                    'dateAdded': 1700000000000,
                    'action': 'AI Resume Summary',
                },
                {  # Human author — must be returned
                    'id': 2,
                    'commentingPerson': {'id': 99999, 'firstName': 'Dan', 'lastName': 'Sifer'},
                    'dateAdded': 1700000060000,
                    'action': 'Recruiter Note',
                },
            ],
            'total': 2,
        }

        with patch('tasks.owner_reassignment._requests') as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = notes_page
            mock_req.get.return_value = mock_resp
            with app.app_context():
                result = _find_first_human_interactor(
                    base_url='https://rest.bullhorn.com/',
                    headers={'BhRestToken': 'tok'},
                    candidate_id=4656051,
                    api_user_ids=[1147490, 4582015, 4582033, 4591841, 4593767],
                )

        assert result == (99999, 'Dan', 'Sifer'), (
            f"Expected to find Dan Sifer as first human interactor; got {result}"
        )

    def test_returns_none_when_only_api_authored_notes(self, app):
        from tasks.owner_reassignment import _find_first_human_interactor

        notes_page = {
            'data': [
                {'id': 1, 'commentingPerson': {'id': 1147490}, 'dateAdded': 1700000000000, 'action': 'a'},
                {'id': 2, 'commentingPerson': {'id': 4582015}, 'dateAdded': 1700000060000, 'action': 'b'},
            ],
            'total': 2,
        }

        with patch('tasks.owner_reassignment._requests') as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = notes_page
            mock_req.get.return_value = mock_resp
            with app.app_context():
                result = _find_first_human_interactor(
                    base_url='https://rest.bullhorn.com/',
                    headers={'BhRestToken': 'tok'},
                    candidate_id=4656965,
                    api_user_ids=[1147490, 4582015],
                )

        assert result == (None, None, None)


# ════════════════════════════════════════════════════════════════════════
# Bug #4 — note-based cooldown bust
# ════════════════════════════════════════════════════════════════════════
# Bullhorn does NOT bump `Candidate.dateLastModified` when a Note is added
# to the candidate (Notes are separate entities with their own
# `dateAdded`). Bug #1's cooldown-bust mechanism therefore couldn't see
# notes added by recruiters during the 24h cooldown window — those
# candidates stayed in cooldown until the timer naturally expired.
#
# `_find_cooldown_busters_via_notes` closes that gap by querying
# Bullhorn's Note search once per cycle and returning the set of
# candidate IDs to bust based on recent non-API notes.
class TestCooldownNoteBuster:
    def test_busts_candidate_when_recent_non_api_note_exists(self, app):
        from datetime import datetime as _dt
        from tasks.owner_reassignment import (
            _find_cooldown_busters_via_notes, _EPOCH,
        )

        last_eval = _dt(2026, 5, 2, 11, 53, 35)
        cooldown_state = {4657858: last_eval}
        api_user_ids = [4582015, 4591841, 4593767, 4582033, 1147490]

        # Note added 1 minute AFTER last_eval by a non-API author (the
        # recruiter who left the note on candidate 4657858 around 12:05).
        note_added_dt = _dt(2026, 5, 2, 11, 54, 35)
        note_added_ms = int((note_added_dt - _EPOCH).total_seconds() * 1000)

        notes_page = {
            'data': [
                {
                    'id': 999,
                    'dateAdded': note_added_ms,
                    'commentingPerson': {'id': 88888},   # human recruiter
                    'personReference': {'id': 4657858},
                },
            ],
            'total': 1,
        }

        captured_query = {}

        def fake_get(url, headers=None, params=None, timeout=None):
            captured_query['query'] = params.get('query', '')
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = notes_page
            return mock_resp

        with patch('tasks.owner_reassignment._requests') as mock_req:
            mock_req.get.side_effect = fake_get
            with app.app_context():
                busters = _find_cooldown_busters_via_notes(
                    base_url='https://rest.bullhorn.com/',
                    headers={'BhRestToken': 'tok'},
                    cooldown_state=cooldown_state,
                    api_user_ids=api_user_ids,
                )

        assert busters == {4657858}, (
            f"Recent non-API note must bust the cooldown for 4657858; got {busters}"
        )
        assert 'isDeleted:false' in captured_query['query']
        assert 'dateAdded:[' in captured_query['query']

    def test_does_not_bust_when_only_api_authored_notes(self, app):
        from datetime import datetime as _dt
        from tasks.owner_reassignment import (
            _find_cooldown_busters_via_notes, _EPOCH,
        )

        last_eval = _dt(2026, 5, 2, 11, 53, 35)
        cooldown_state = {4657858: last_eval}
        api_user_ids = [4582015, 4591841, 4593767, 4582033, 1147490]

        note_added_ms = int(
            (_dt(2026, 5, 2, 11, 54, 35) - _EPOCH).total_seconds() * 1000
        )
        notes_page = {
            'data': [
                {
                    'id': 1,
                    'dateAdded': note_added_ms,
                    'commentingPerson': {'id': 1147490},   # Myticas API
                    'personReference': {'id': 4657858},
                },
                {
                    'id': 2,
                    'dateAdded': note_added_ms + 1000,
                    'commentingPerson': {'id': 4582033},   # Pandologic
                    'personReference': {'id': 4657858},
                },
            ],
            'total': 2,
        }

        with patch('tasks.owner_reassignment._requests') as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = notes_page
            mock_req.get.return_value = mock_resp
            with app.app_context():
                busters = _find_cooldown_busters_via_notes(
                    base_url='https://rest.bullhorn.com/',
                    headers={'BhRestToken': 'tok'},
                    cooldown_state=cooldown_state,
                    api_user_ids=api_user_ids,
                )

        assert busters == set(), (
            f"API-authored notes must NOT bust cooldowns; got {busters}"
        )

    def test_does_not_bust_when_note_predates_last_evaluated_at(self, app):
        from datetime import datetime as _dt
        from tasks.owner_reassignment import (
            _find_cooldown_busters_via_notes, _EPOCH,
        )

        # Candidate A: last_eval=12:00, note at 11:55 → must NOT bust
        # Candidate B: last_eval=11:00, note at 11:55 → MUST bust
        # Both notes returned in the same Bullhorn page; per-candidate
        # comparison must be precise so A is not falsely busted by B's
        # bust-eligible note appearing in the same response.
        cooldown_state = {
            10001: _dt(2026, 5, 2, 12, 0, 0),
            10002: _dt(2026, 5, 2, 11, 0, 0),
        }
        api_user_ids = [1147490]

        old_note_ms = int(
            (_dt(2026, 5, 2, 11, 55, 0) - _EPOCH).total_seconds() * 1000
        )

        notes_page = {
            'data': [
                {
                    'id': 1,
                    'dateAdded': old_note_ms,
                    'commentingPerson': {'id': 88888},
                    'personReference': {'id': 10001},   # predates A's eval
                },
                {
                    'id': 2,
                    'dateAdded': old_note_ms,
                    'commentingPerson': {'id': 88888},
                    'personReference': {'id': 10002},   # newer than B's eval
                },
            ],
            'total': 2,
        }

        with patch('tasks.owner_reassignment._requests') as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = notes_page
            mock_req.get.return_value = mock_resp
            with app.app_context():
                busters = _find_cooldown_busters_via_notes(
                    base_url='https://rest.bullhorn.com/',
                    headers={'BhRestToken': 'tok'},
                    cooldown_state=cooldown_state,
                    api_user_ids=api_user_ids,
                )

        assert busters == {10002}, (
            f"Per-candidate timestamp comparison must be precise; "
            f"expected {{10002}}, got {busters}"
        )

    def test_fail_open_on_bullhorn_error(self, app):
        from datetime import datetime as _dt
        from tasks.owner_reassignment import _find_cooldown_busters_via_notes

        cooldown_state = {4657858: _dt(2026, 5, 2, 11, 53, 35)}

        with patch('tasks.owner_reassignment._requests') as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.text = 'Internal Server Error'
            mock_req.get.return_value = mock_resp
            with app.app_context():
                busters = _find_cooldown_busters_via_notes(
                    base_url='https://rest.bullhorn.com/',
                    headers={'BhRestToken': 'tok'},
                    cooldown_state=cooldown_state,
                    api_user_ids=[1147490],
                )

        assert busters == set(), (
            f"Helper must fail-open on Bullhorn errors; got {busters}"
        )

    def test_returns_empty_when_cooldown_state_empty(self, app):
        from tasks.owner_reassignment import _find_cooldown_busters_via_notes

        with patch('tasks.owner_reassignment._requests') as mock_req:
            with app.app_context():
                busters = _find_cooldown_busters_via_notes(
                    base_url='https://rest.bullhorn.com/',
                    headers={'BhRestToken': 'tok'},
                    cooldown_state={},
                    api_user_ids=[1147490],
                )

        assert busters == set()
        # Critical: must not call Bullhorn at all when there are no
        # cooldown rows to evaluate.
        assert not mock_req.get.called, (
            "Empty cooldown_state must short-circuit without any API call"
        )

    def test_newest_first_sort_catches_recent_busters_under_cap(self, app):
        """High-volume regression test for the fix architect flagged.

        If notes between ``floor_ms`` and now exceed the pagination cap
        (10 × 200 = 2,000) and we sort ascending, the scan would chew
        through ancient notes first and never reach the recent recruiter
        note that should bust cooldown — recreating the bug-#4 blind
        spot. This test asserts the helper requests newest-first
        ordering AND correctly catches a recent buster on page 0 even
        when the reported total far exceeds the cap.
        """
        from datetime import datetime as _dt
        from tasks.owner_reassignment import (
            _find_cooldown_busters_via_notes, _EPOCH,
        )

        cid = 4657858
        cooldown_state = {cid: _dt(2026, 5, 2, 11, 53, 35)}
        cooldown_ms = int(
            (cooldown_state[cid] - _EPOCH).total_seconds() * 1000
        )
        # A genuinely recent recruiter note — would bust cooldown.
        recent_buster_note = {
            'id': 999001,
            'dateAdded': cooldown_ms + 60_000,  # 1 min after last_eval
            'commentingPerson': {'id': 1147490},  # human recruiter
            'personReference': {'id': cid},
        }
        # Page 0 (newest-first) returns the buster. Total is huge to
        # simulate "10,000 notes exist in the window but the cap is 2,000."
        page0 = {'data': [recent_buster_note], 'total': 10_000}

        with patch('tasks.owner_reassignment._requests') as mock_req:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = page0
            mock_req.get.return_value = mock_resp
            with app.app_context():
                busters = _find_cooldown_busters_via_notes(
                    base_url='https://rest.bullhorn.com/',
                    headers={'BhRestToken': 'tok'},
                    cooldown_state=cooldown_state,
                    api_user_ids=[4582015, 4591841],
                )

        assert cid in busters, (
            "Recent recruiter note on page 0 must bust the cooldown "
            "even when total exceeds the pagination cap"
        )
        # Verify we requested newest-first ordering — this is the
        # architectural guarantee that high-volume orgs don't get
        # blind-spotted.
        first_call_kwargs = mock_req.get.call_args_list[0].kwargs
        params = first_call_kwargs.get('params', {})
        assert params.get('sort') == '-dateAdded', (
            f"Note search must sort newest-first to survive the "
            f"pagination cap; got sort={params.get('sort')!r}"
        )
