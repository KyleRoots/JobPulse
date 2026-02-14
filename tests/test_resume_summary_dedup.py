"""
Tests for AI Resume Summary duplicate prevention.

Tests the _check_existing_resume_summary() method in EmailInboundService
which mirrors the vetting dedup pattern (24h window + action filter).
"""

import sys
import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta


class ColumnMock:
    """A plain class (NOT a MagicMock subclass) that supports comparison operators
    like SQLAlchemy Column objects.
    
    MagicMock's metaclass intercepts dunder methods and prevents overrides from
    working, so MagicMock subclasses can't support `col >= datetime(...)`.
    This plain class provides the comparison support needed for filter() args.
    """
    def __ge__(self, other): return MagicMock()
    def __le__(self, other): return MagicMock()
    def __gt__(self, other): return MagicMock()
    def __lt__(self, other): return MagicMock()
    def __eq__(self, other): return MagicMock()
    def __ne__(self, other): return MagicMock()
    def __hash__(self): return id(self)
    
    def isnot(self, other):
        return MagicMock()
    
    def desc(self):
        return MagicMock()
    
    def asc(self):
        return MagicMock()


@pytest.fixture
def service():
    """Create an EmailInboundService instance with mocked OpenAI."""
    from email_inbound_service import EmailInboundService
    svc = EmailInboundService.__new__(EmailInboundService)
    svc.openai_client = None
    svc.logger = MagicMock()
    return svc


@pytest.fixture
def mock_bullhorn():
    """Create a mock BullhornService."""
    bh = MagicMock()
    bh.get_candidate_notes = MagicMock(return_value=[])
    return bh


def _build_mock_pe_module(filenames: list):
    """
    Build a mock 'models' module with a ParsedEmail class whose
    query.filter().order_by().all() returns mock records with the
    given filenames. Needed because _check_existing_resume_summary()
    does `from models import ParsedEmail as PE` as a local import.
    
    The mock supports SQLAlchemy-style filter expressions like:
        PE.processed_at >= datetime(...)  — returns a MagicMock (truthy)
        PE.resume_filename.isnot(None)   — returns a MagicMock (truthy)
    """
    mock_pe_cls = MagicMock()
    mock_records = []
    for fn in filenames:
        rec = MagicMock()
        rec.resume_filename = fn
        mock_records.append(rec)

    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.all.return_value = mock_records
    mock_pe_cls.query = mock_query

    # Use ColumnMock for attributes used in SQLAlchemy filter expressions
    # so that PE.processed_at >= datetime(...) returns a MagicMock instead of raising
    mock_pe_cls.processed_at = ColumnMock()
    mock_pe_cls.bullhorn_candidate_id = ColumnMock()
    mock_pe_cls.status = ColumnMock()
    mock_pe_cls.resume_filename = ColumnMock()

    # Create a mock module that exposes ParsedEmail
    mock_models_module = MagicMock()
    mock_models_module.ParsedEmail = mock_pe_cls
    return mock_models_module


class TestCheckExistingResumeSummary:
    """Tests for _check_existing_resume_summary()."""

    def test_no_existing_summary_allows_creation(self, service, mock_bullhorn):
        """First application — no existing notes, should allow creation."""
        mock_bullhorn.get_candidate_notes.return_value = []

        result = service._check_existing_resume_summary(
            mock_bullhorn, candidate_id=12345, current_resume_filename="resume.pdf"
        )

        assert result is False  # Safe to create
        mock_bullhorn.get_candidate_notes.assert_called_once()
        # Verify correct action_filter was used
        call_kwargs = mock_bullhorn.get_candidate_notes.call_args
        assert call_kwargs[1].get('action_filter') == ["AI Resume Summary"] or \
               call_kwargs[0][1] if len(call_kwargs[0]) > 1 else True

    def test_existing_summary_same_resume_skips(self, service, mock_bullhorn):
        """Same resume within 24h — should skip (duplicate)."""
        mock_bullhorn.get_candidate_notes.return_value = [
            {'id': 100, 'action': 'AI Resume Summary', 'dateAdded': int(datetime.utcnow().timestamp() * 1000)}
        ]

        mock_models = _build_mock_pe_module(["resume.pdf"])
        mock_app = MagicMock()

        with patch.dict(sys.modules, {'models': mock_models, 'app': mock_app}):
            result = service._check_existing_resume_summary(
                mock_bullhorn, candidate_id=12345, current_resume_filename="resume.pdf"
            )

        assert result is True  # Should skip — duplicate

    def test_existing_summary_different_resume_allows(self, service, mock_bullhorn):
        """Different resume within 24h — should allow new summary."""
        mock_bullhorn.get_candidate_notes.return_value = [
            {'id': 100, 'action': 'AI Resume Summary', 'dateAdded': int(datetime.utcnow().timestamp() * 1000)}
        ]

        mock_models = _build_mock_pe_module(["old_resume.pdf"])
        mock_app = MagicMock()

        with patch.dict(sys.modules, {'models': mock_models, 'app': mock_app}):
            result = service._check_existing_resume_summary(
                mock_bullhorn, candidate_id=12345, current_resume_filename="new_resume.pdf"
            )

        assert result is False  # Safe to create — new resume

    def test_dedup_check_failure_allows_creation(self, service, mock_bullhorn):
        """If the dedup check itself errors, should fail-safe and allow creation."""
        mock_bullhorn.get_candidate_notes.side_effect = Exception("API timeout")

        result = service._check_existing_resume_summary(
            mock_bullhorn, candidate_id=12345, current_resume_filename="resume.pdf"
        )

        assert result is False  # Fail-safe: allow creation
        service.logger.warning.assert_called_once()

    def test_no_resume_filename_enforces_24h_rule(self, service, mock_bullhorn):
        """No filename available — should enforce simple 24h fallback rule."""
        mock_bullhorn.get_candidate_notes.return_value = [
            {'id': 100, 'action': 'AI Resume Summary', 'dateAdded': int(datetime.utcnow().timestamp() * 1000)}
        ]

        result = service._check_existing_resume_summary(
            mock_bullhorn, candidate_id=12345, current_resume_filename=None
        )

        assert result is True  # Should skip — 24h fallback rule

    def test_summary_older_than_24h_allows_creation(self, service, mock_bullhorn):
        """Existing summary > 24h old — should allow new creation."""
        # get_candidate_notes with since=24h ago returns empty (note is older)
        mock_bullhorn.get_candidate_notes.return_value = []

        result = service._check_existing_resume_summary(
            mock_bullhorn, candidate_id=12345, current_resume_filename="resume.pdf"
        )

        assert result is False  # Safe to create — old summary expired


class TestCanonicalNoteSelection:
    """Tests for the canonical note selection logic in the cleanup script."""

    def test_canonical_with_vetting_note(self):
        """Should keep the resume summary immediately before the vetting note."""
        from scripts.cleanup_resume_summary_duplicates import find_canonical_note

        resume_notes = [
            {'id': 1, 'action': 'AI Resume Summary', 'dateAdded': 1000},
            {'id': 2, 'action': 'AI Resume Summary', 'dateAdded': 2000},
            {'id': 3, 'action': 'AI Resume Summary', 'dateAdded': 4000},
        ]
        vetting_notes = [
            {'id': 10, 'action': 'AI Vetting - Qualified', 'dateAdded': 3000},
        ]

        canonical = find_canonical_note(resume_notes, vetting_notes)

        # Note 2 (dateAdded=2000) is the last one before vetting (dateAdded=3000)
        assert canonical['id'] == 2

    def test_canonical_without_vetting_note(self):
        """Without vetting notes, should keep the most recent resume summary."""
        from scripts.cleanup_resume_summary_duplicates import find_canonical_note

        resume_notes = [
            {'id': 1, 'action': 'AI Resume Summary', 'dateAdded': 1000},
            {'id': 2, 'action': 'AI Resume Summary', 'dateAdded': 2000},
            {'id': 3, 'action': 'AI Resume Summary', 'dateAdded': 3000},
        ]

        canonical = find_canonical_note(resume_notes, [])

        # Most recent = note 3
        assert canonical['id'] == 3

    def test_canonical_single_note(self):
        """Single note should always be the canonical one."""
        from scripts.cleanup_resume_summary_duplicates import find_canonical_note

        resume_notes = [
            {'id': 1, 'action': 'AI Resume Summary', 'dateAdded': 1000},
        ]

        canonical = find_canonical_note(resume_notes, [])
        assert canonical['id'] == 1

    def test_canonical_empty_list(self):
        """Empty list should return None."""
        from scripts.cleanup_resume_summary_duplicates import find_canonical_note

        canonical = find_canonical_note([], [])
        assert canonical is None

    def test_canonical_all_notes_after_vetting(self):
        """If all resume notes are after the vetting note, keep the most recent."""
        from scripts.cleanup_resume_summary_duplicates import find_canonical_note

        resume_notes = [
            {'id': 1, 'action': 'AI Resume Summary', 'dateAdded': 5000},
            {'id': 2, 'action': 'AI Resume Summary', 'dateAdded': 6000},
        ]
        vetting_notes = [
            {'id': 10, 'action': 'AI Vetting - Qualified', 'dateAdded': 3000},
        ]

        canonical = find_canonical_note(resume_notes, vetting_notes)

        # No notes before vetting — fallback to most recent
        assert canonical['id'] == 2
