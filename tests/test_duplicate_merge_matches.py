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
