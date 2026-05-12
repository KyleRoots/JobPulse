"""Test the archive-redirect guard in email_inbound_service.ai_mixin.

Covers the live bug where a 5/11/2026 application for Ram Pathak landed on
archived BH ID 4020713 instead of merge-winner BH ID 4452544.

Two layers of defense are tested:
  1. bullhorn_service.search_candidates appends `-status:Archive` to the query
     so archives don't even come back.
  2. email_inbound_service._resolve_archive_redirect catches any archived
     match that does slip through, and either redirects to the live winner
     via CandidateMergeLog or skips the match entirely.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1: bullhorn search query filter
# ─────────────────────────────────────────────────────────────────────────────

def test_search_candidates_excludes_archives_by_default():
    """Default include_archived=False appends `-status:Archive` to the query."""
    from bullhorn_service.candidates import CandidatesMixin

    svc = CandidatesMixin()
    svc.base_url = 'https://example/'
    svc.rest_token = 'tok'
    svc.session = MagicMock()
    svc.session.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': []},
    )
    svc._safe_json_parse = lambda r: r.json()

    svc.search_candidates(email='ram@example.com')

    sent_query = svc.session.get.call_args.kwargs['params']['query']
    assert '-status:Archive' in sent_query
    assert '-isDeleted:1' in sent_query
    assert 'ram@example.com' in sent_query


def test_search_candidates_can_include_archives_when_explicit():
    """include_archived=True drops the filter (for diagnostic flows)."""
    from bullhorn_service.candidates import CandidatesMixin

    svc = CandidatesMixin()
    svc.base_url = 'https://example/'
    svc.rest_token = 'tok'
    svc.session = MagicMock()
    svc.session.get.return_value = MagicMock(
        status_code=200,
        json=lambda: {'data': []},
    )
    svc._safe_json_parse = lambda r: r.json()

    svc.search_candidates(email='ram@example.com', include_archived=True)

    sent_query = svc.session.get.call_args.kwargs['params']['query']
    assert '-status:Archive' not in sent_query
    assert '-isDeleted:1' not in sent_query


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2: _resolve_archive_redirect helper
# ─────────────────────────────────────────────────────────────────────────────

def _build_mixin():
    """Construct a bare AIMixin instance with a logger attached."""
    from email_inbound_service.ai_mixin import AIMixin

    inst = AIMixin.__new__(AIMixin)
    inst.logger = MagicMock()
    return inst


def test_resolve_redirect_passes_through_non_archived_match():
    inst = _build_mixin()
    match = {'id': 4452544, 'status': 'Placed'}
    assert inst._resolve_archive_redirect(match) == 4452544


def test_resolve_redirect_passes_through_when_status_missing():
    inst = _build_mixin()
    match = {'id': 4452544}
    assert inst._resolve_archive_redirect(match) == 4452544


def test_resolve_redirect_redirects_archived_to_winner_via_merge_log():
    """The Ram Pathak case: archived 4020713 → live winner 4452544."""
    inst = _build_mixin()
    archived_match = {'id': 4020713, 'status': 'Archive'}

    fake_log_row = MagicMock()
    fake_log_row.primary_candidate_id = 4452544
    fake_log_row.merged_at = '2026-04-14 12:51:00'
    fake_log_row.match_type = 'exact'

    mock_models = MagicMock()
    mock_log_cls = MagicMock()
    mock_log_cls.query.filter_by.return_value.order_by.return_value.first.return_value = fake_log_row
    mock_models.CandidateMergeLog = mock_log_cls

    with patch.dict('sys.modules', {'models': mock_models}):
        result = inst._resolve_archive_redirect(archived_match)
        assert result == 4452544

    assert any('🔀 ARCHIVE REDIRECT' in str(c)
               for c in inst.logger.warning.call_args_list)


def test_resolve_redirect_follows_chained_merge():
    """Chained merge A→B→C: archived A redirects to B, B is also archived,
    chain follows to C which is live. (Architect-flagged edge case.)"""
    inst = _build_mixin()
    archived_a = {'id': 100, 'status': 'Archive'}

    # Chain: 100 → 200 → 300 (300 is live)
    chain_map = {100: 200, 200: 300}

    def fake_filter_by(duplicate_candidate_id, skipped):
        winner = chain_map.get(duplicate_candidate_id)
        if winner is None:
            row_result = MagicMock()
            row_result.order_by.return_value.first.return_value = None
            return row_result
        row = MagicMock()
        row.primary_candidate_id = winner
        row.merged_at = '2026-01-01'
        row.match_type = 'exact'
        row_result = MagicMock()
        row_result.order_by.return_value.first.return_value = row
        return row_result

    mock_models = MagicMock()
    mock_log_cls = MagicMock()
    mock_log_cls.query.filter_by.side_effect = fake_filter_by
    mock_models.CandidateMergeLog = mock_log_cls

    bh = MagicMock()
    bh.get_candidate_by_id = lambda cid: {200: {'status': 'Archive'},
                                          300: {'status': 'Placed'}}.get(cid)

    with patch.dict('sys.modules', {'models': mock_models}):
        result = inst._resolve_archive_redirect(archived_a, bullhorn_service=bh)
        assert result == 300


def test_resolve_redirect_breaks_loop_in_corrupt_chain():
    """If a merge log forms a cycle (A→B, B→A), abort cleanly."""
    inst = _build_mixin()
    archived_a = {'id': 100, 'status': 'Archive'}

    chain_map = {100: 200, 200: 100}  # cycle

    def fake_filter_by(duplicate_candidate_id, skipped):
        winner = chain_map.get(duplicate_candidate_id)
        row = MagicMock()
        row.primary_candidate_id = winner
        row.merged_at = '2026-01-01'
        row.match_type = 'exact'
        row_result = MagicMock()
        row_result.order_by.return_value.first.return_value = row
        return row_result

    mock_models = MagicMock()
    mock_log_cls = MagicMock()
    mock_log_cls.query.filter_by.side_effect = fake_filter_by
    mock_models.CandidateMergeLog = mock_log_cls

    bh = MagicMock()
    bh.get_candidate_by_id = lambda cid: {'status': 'Archive'}

    with patch.dict('sys.modules', {'models': mock_models}):
        result = inst._resolve_archive_redirect(archived_a, bullhorn_service=bh)
        assert result is None

    assert any('LOOP' in str(c) for c in inst.logger.warning.call_args_list)


def test_resolve_redirect_blocks_archived_with_no_merge_log():
    """No merge log row → return None so caller skips this match."""
    inst = _build_mixin()
    archived_match = {'id': 4020713, 'status': 'Archive'}

    mock_models = MagicMock()
    mock_log_cls = MagicMock()
    mock_log_cls.query.filter_by.return_value.order_by.return_value.first.return_value = None
    mock_models.CandidateMergeLog = mock_log_cls

    with patch.dict('sys.modules', {'models': mock_models}):
        result = inst._resolve_archive_redirect(archived_match)
        assert result is None

    assert any(
        '⛔ ARCHIVE BLOCK' in str(c)
        for c in inst.logger.warning.call_args_list
    )


def test_resolve_redirect_status_check_is_case_insensitive():
    """status='ARCHIVE' / 'archive' / ' Archive ' all trigger the guard."""
    inst = _build_mixin()
    mock_models = MagicMock()
    mock_log_cls = MagicMock()
    mock_log_cls.query.filter_by.return_value.order_by.return_value.first.return_value = None
    mock_models.CandidateMergeLog = mock_log_cls

    with patch.dict('sys.modules', {'models': mock_models}):
        for variant in ('Archive', 'ARCHIVE', 'archive', '  Archive  '):
            inst.logger.reset_mock()
            assert inst._resolve_archive_redirect({'id': 99, 'status': variant}) is None


def test_resolve_redirect_failsafe_on_db_exception():
    """If the merge log query blows up, the guard still blocks the write
    rather than silently allowing it through (don't trade one bug for another)."""
    inst = _build_mixin()
    archived_match = {'id': 4020713, 'status': 'Archive'}

    mock_models = MagicMock()
    mock_log_cls = MagicMock()
    mock_log_cls.query.filter_by.side_effect = RuntimeError('db connection lost')
    mock_models.CandidateMergeLog = mock_log_cls

    with patch.dict('sys.modules', {'models': mock_models}):
        result = inst._resolve_archive_redirect(archived_match)
        assert result is None  # blocked, not passed through

    assert any('Archive-redirect lookup failed' in str(c)
               for c in inst.logger.warning.call_args_list)
