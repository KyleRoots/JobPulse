"""Tests for duplicate_merge_service._find_matches_for_candidate.

Verifies that the email-search and phone-search paths run independently
and that combined results are deduplicated by candidate id.
"""
from unittest.mock import MagicMock, patch

import pytest

from duplicate_merge_service import DuplicateMergeService


def _mock_response(data, status=200):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = {'data': data}
    return resp


def _make_service(email_results, phone_results):
    """Build a DuplicateMergeService whose Bullhorn session returns the
    given results for the email-search call and phone-search call in order.
    """
    service = DuplicateMergeService()
    fake_bh = MagicMock()
    fake_bh.base_url = 'https://example.invalid/'
    fake_bh.rest_token = 'test-token'

    call_log = []

    def fake_get(url, params=None, timeout=None):
        query = (params or {}).get('query', '')
        call_log.append(query)
        if 'email' in query and 'phone' not in query and 'mobile' not in query:
            return _mock_response(email_results)
        if 'phone' in query or 'mobile' in query:
            return _mock_response(phone_results)
        return _mock_response([])

    fake_bh.session.get.side_effect = fake_get
    service._bullhorn = fake_bh
    service._call_log = call_log
    return service


def test_phone_search_runs_even_when_email_returns_results():
    """The old behavior gated phone-search behind 'email returned nothing'.
    The fix: phone-search must always run when phone digits are present,
    so duplicates that share a phone but have a different email are caught.
    """
    candidate = {
        'id': 100,
        'email': 'jane@example.com',
        'phone': '5551234567',
        'mobile': '',
    }
    email_match = {'id': 200, 'email': 'jane@example.com', 'firstName': 'Jane',
                   'lastName': 'Doe', 'status': 'Active'}
    phone_match = {'id': 300, 'email': 'different@example.com', 'phone': '5551234567',
                   'firstName': 'Jane', 'lastName': 'Doe', 'status': 'Active'}

    service = _make_service([email_match], [phone_match])
    matches = service._find_matches_for_candidate(candidate)

    ids = sorted(m.get('id') for m in matches)
    assert ids == [200, 300], f"Both email and phone matches should appear, got {ids}"
    assert len(service._call_log) == 2, "Both email and phone searches should fire"


def test_combined_results_are_deduplicated_by_id():
    """If email-search and phone-search return the same candidate id,
    it must appear only once in the combined match list."""
    candidate = {
        'id': 100,
        'email': 'jane@example.com',
        'phone': '5551234567',
        'mobile': '',
    }
    shared = {'id': 200, 'email': 'jane@example.com', 'phone': '5551234567',
              'firstName': 'Jane', 'lastName': 'Doe', 'status': 'Active'}

    service = _make_service([shared], [shared])
    matches = service._find_matches_for_candidate(candidate)

    assert [m.get('id') for m in matches] == [200], \
        f"Shared candidate should appear once, got {[m.get('id') for m in matches]}"


def test_self_is_excluded_from_matches():
    """The candidate's own id must never appear in its match list."""
    candidate = {
        'id': 100,
        'email': 'jane@example.com',
        'phone': '5551234567',
        'mobile': '',
    }
    self_record = {'id': 100, 'email': 'jane@example.com', 'phone': '5551234567',
                   'firstName': 'Jane', 'lastName': 'Doe', 'status': 'Active'}
    other = {'id': 200, 'email': 'jane@example.com', 'firstName': 'Jane',
             'lastName': 'Doe', 'status': 'Active'}

    service = _make_service([self_record, other], [self_record])
    matches = service._find_matches_for_candidate(candidate)

    ids = [m.get('id') for m in matches]
    assert 100 not in ids, f"Self id leaked into matches: {ids}"
    assert ids == [200], f"Only the non-self match should appear, got {ids}"


def test_archived_candidates_are_excluded():
    """Archive-status candidates must be filtered out."""
    candidate = {
        'id': 100,
        'email': 'jane@example.com',
        'phone': '',
        'mobile': '',
    }
    archived = {'id': 200, 'email': 'jane@example.com', 'firstName': 'Jane',
                'lastName': 'Doe', 'status': 'Archive'}
    active = {'id': 300, 'email': 'jane@example.com', 'firstName': 'Jane',
              'lastName': 'Doe', 'status': 'Active'}

    service = _make_service([archived, active], [])
    matches = service._find_matches_for_candidate(candidate)

    ids = [m.get('id') for m in matches]
    assert ids == [300], f"Archive-status candidate should be filtered out, got {ids}"


def test_phone_search_includes_both_phone_and_mobile_when_distinct():
    """When candidate has both a distinct phone and mobile (each >=10 digits),
    the search query should include both numbers, not just one."""
    candidate = {
        'id': 100,
        'email': '',
        'phone': '5551234567',
        'mobile': '5559998888',
    }
    service = _make_service([], [])
    service._find_matches_for_candidate(candidate)

    # Only the phone search should have fired (no email)
    assert len(service._call_log) == 1
    phone_query = service._call_log[0]
    assert '5551234567' in phone_query, f"Phone digits missing: {phone_query}"
    assert '5559998888' in phone_query, f"Mobile digits missing: {phone_query}"


def test_no_search_runs_when_no_email_and_no_valid_phone():
    """If email is empty and both phones are <10 digits, no API call is made."""
    candidate = {
        'id': 100,
        'email': '',
        'phone': '123',
        'mobile': '',
    }
    service = _make_service([], [])
    matches = service._find_matches_for_candidate(candidate)

    assert matches == []
    assert service._call_log == []


# ---------------------------------------------------------------------------
# Ownership-preserving auto-merge (determine_primary + note authorship).
# In a no-app-context test run VettingConfig lookups fail-soft, so the API
# user set collapses to the built-in {1147490}.
# ---------------------------------------------------------------------------
API_USER_ID = 1147490


def _service_no_placements():
    service = DuplicateMergeService()
    service._bullhorn = MagicMock()
    # No active placements unless a test overrides this.
    service._has_active_placement = lambda cid: False
    return service


def test_determine_primary_prefers_human_owner_over_api_user():
    """A fresh API-user-owned record (newest dateAdded) must NOT win over a
    recruiter-owned record — the human-owned one survives."""
    service = _service_no_placements()
    human = {'id': 200, 'dateAdded': 1000, 'owner': {'id': 555}}      # older, human
    api = {'id': 300, 'dateAdded': 9999, 'owner': {'id': API_USER_ID}}  # newer, API

    primary, duplicate, reason = service.determine_primary(api, human)

    assert primary['id'] == 200, "Human-owned record should survive as primary"
    assert duplicate['id'] == 300
    assert reason == 'human_owner'


def test_determine_primary_active_placement_beats_human_owner():
    """Active placement stays top priority: a placed record survives even if the
    other record is human-owned."""
    service = DuplicateMergeService()
    service._bullhorn = MagicMock()
    service._has_active_placement = lambda cid: cid == 300

    human = {'id': 200, 'dateAdded': 1000, 'owner': {'id': 555}}
    api = {'id': 300, 'dateAdded': 9999, 'owner': {'id': API_USER_ID}}

    primary, duplicate, reason = service.determine_primary(human, api)

    assert primary['id'] == 300
    assert reason == 'active_placement'


def test_determine_primary_recency_when_both_human():
    """When both records are human-owned, fall back to existing recency logic."""
    service = _service_no_placements()
    older = {'id': 200, 'dateAdded': 1000, 'owner': {'id': 555}}
    newer = {'id': 300, 'dateAdded': 9999, 'owner': {'id': 777}}

    primary, _, reason = service.determine_primary(older, newer)

    assert primary['id'] == 300
    assert reason == 'most_recent'


def test_determine_primary_recency_when_both_api_owned():
    """When both records are API-user-owned, fall back to recency unchanged."""
    service = _service_no_placements()
    older = {'id': 200, 'dateAdded': 1000, 'owner': {'id': API_USER_ID}}
    newer = {'id': 300, 'dateAdded': 9999, 'owner': {'id': API_USER_ID}}

    primary, _, reason = service.determine_primary(older, newer)

    assert primary['id'] == 300
    assert reason == 'most_recent'


def _service_capturing_note_put():
    service = DuplicateMergeService()
    fake_bh = MagicMock()
    fake_bh.base_url = 'https://example.invalid/'
    fake_bh.rest_token = 'tok'
    fake_bh.user_id = API_USER_ID
    captured = {}

    def fake_put(url, json=None, params=None, timeout=None):
        captured['payload'] = json
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {'changedEntityId': 42}
        return resp

    fake_bh.session.put.side_effect = fake_put
    service._bullhorn = fake_bh
    service._captured = captured
    return service


def test_transfer_note_preserves_original_commenting_person():
    """A transferred note keeps its original author instead of being re-stamped
    to the acting API user."""
    service = _service_capturing_note_put()
    note = {'id': 9, 'action': 'Prescreen', 'comments': 'hi',
            'commentingPerson': {'id': 555}}

    assert service._transfer_note(777, note) is True
    assert service._captured['payload']['commentingPerson'] == {'id': 555}


def test_transfer_note_falls_back_to_api_user_when_author_unknown():
    """When the source note has no commentingPerson, fall back to the API user."""
    service = _service_capturing_note_put()
    note = {'id': 9, 'action': 'Note', 'comments': 'x'}  # no commentingPerson

    assert service._transfer_note(777, note) is True
    assert service._captured['payload']['commentingPerson'] == {'id': API_USER_ID}


def test_preserve_human_owner_restores_when_survivor_is_api_owned():
    """Active-placement edge: survivor is API-owned but the archived duplicate
    was human-owned -> restore the human owner onto the survivor."""
    service = DuplicateMergeService()
    fake_bh = MagicMock()
    service._bullhorn = fake_bh
    primary = {'id': 300, 'owner': {'id': API_USER_ID}}
    duplicate = {'id': 200, 'owner': {'id': 555}}

    service._preserve_human_owner(primary, duplicate)

    fake_bh.update_candidate.assert_called_once_with(300, {'owner': {'id': 555}})


def test_preserve_human_owner_noop_when_survivor_already_human():
    """No owner write when the survivor is already human-owned."""
    service = DuplicateMergeService()
    fake_bh = MagicMock()
    service._bullhorn = fake_bh
    primary = {'id': 300, 'owner': {'id': 555}}
    duplicate = {'id': 200, 'owner': {'id': API_USER_ID}}

    service._preserve_human_owner(primary, duplicate)

    fake_bh.update_candidate.assert_not_called()


def test_preserve_human_owner_fails_closed_when_survivor_owner_unresolved():
    """If the survivor's owner can't be confidently resolved (no 'owner' key and
    the live lookup returns nothing), do NOT overwrite — fail closed."""
    service = DuplicateMergeService()
    fake_bh = MagicMock()
    fake_bh.get_candidate.return_value = None  # soft failure / not found
    service._bullhorn = fake_bh
    primary = {'id': 300}  # no 'owner' key -> must fetch
    duplicate = {'id': 200, 'owner': {'id': 555}}  # human-owned

    service._preserve_human_owner(primary, duplicate)

    fake_bh.update_candidate.assert_not_called()


def test_preserve_human_owner_restores_when_survivor_owner_fetched_as_api():
    """When the survivor lacks an embedded owner but the live lookup confidently
    reports an API-user owner, restore the archived duplicate's human owner."""
    service = DuplicateMergeService()
    fake_bh = MagicMock()
    fake_bh.get_candidate.return_value = {'id': 300, 'owner': {'id': API_USER_ID}}
    service._bullhorn = fake_bh
    primary = {'id': 300}  # no 'owner' key -> fetch resolves to API user
    duplicate = {'id': 200, 'owner': {'id': 555}}

    service._preserve_human_owner(primary, duplicate)

    fake_bh.update_candidate.assert_called_once_with(300, {'owner': {'id': 555}})
