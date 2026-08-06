"""Tests for Scout note duplicate-safeguard outcome supersession.

Regression: candidate 4553046 (Femi Oyesanya) — auditor re-vet flipped
Not Qualified (69%) → Qualified (84%). Email went out; Bullhorn note stayed
Not Qualified because the 6h dedupe blocked same-window writes.
"""

from types import SimpleNamespace

from screening.note_builder import (
    classify_scout_note_action,
    intended_scout_note_outcome,
    should_supersede_existing_notes,
    _NOTE_OUTCOME_QUALIFIED,
    _NOTE_OUTCOME_NOT_QUALIFIED,
    _NOTE_OUTCOME_LOCATION_REVIEW,
    _NOTE_OUTCOME_INCOMPLETE,
)


class TestClassifyScoutNoteAction:
    def test_qualified_variants(self):
        assert classify_scout_note_action('Scout Screen - Qualified') == _NOTE_OUTCOME_QUALIFIED
        assert classify_scout_note_action('AI Vetting - Qualified') == _NOTE_OUTCOME_QUALIFIED

    def test_not_qualified_before_qualified_substring(self):
        assert classify_scout_note_action('Scout Screen - Not Qualified') == _NOTE_OUTCOME_NOT_QUALIFIED
        assert classify_scout_note_action('Scout Screening - Not Recommended') == _NOTE_OUTCOME_NOT_QUALIFIED
        assert classify_scout_note_action('AI Vetting - Not Recommended') == _NOTE_OUTCOME_NOT_QUALIFIED

    def test_location_and_incomplete(self):
        assert classify_scout_note_action('Scout Screen - Location Review') == _NOTE_OUTCOME_LOCATION_REVIEW
        assert classify_scout_note_action('Scout Screen - Incomplete') == _NOTE_OUTCOME_INCOMPLETE


class TestIntendedScoutNoteOutcome:
    def test_qualified_match(self):
        matches = [SimpleNamespace(is_qualified=True, match_summary='fit', match_score=84)]
        assert intended_scout_note_outcome(matches) == _NOTE_OUTCOME_QUALIFIED

    def test_not_qualified(self):
        matches = [SimpleNamespace(is_qualified=False, match_summary='gaps', match_score=69)]
        assert intended_scout_note_outcome(matches) == _NOTE_OUTCOME_NOT_QUALIFIED

    def test_empty_is_incomplete(self):
        assert intended_scout_note_outcome([]) == _NOTE_OUTCOME_INCOMPLETE

    def test_analysis_failed_is_incomplete(self):
        matches = [
            SimpleNamespace(
                is_qualified=False,
                match_summary='Analysis failed: timeout',
                match_score=0,
            )
        ]
        assert intended_scout_note_outcome(matches) == _NOTE_OUTCOME_INCOMPLETE


class TestShouldSupersedeExistingNotes:
    def test_no_existing_allows_write(self):
        allow, reason = should_supersede_existing_notes(
            [], _NOTE_OUTCOME_QUALIFIED, has_match_records=True
        )
        assert allow is True
        assert reason == 'no_existing'

    def test_same_outcome_blocked(self):
        existing = [{'action': 'Scout Screen - Not Qualified', 'comments': '69%'}]
        allow, reason = should_supersede_existing_notes(
            existing, _NOTE_OUTCOME_NOT_QUALIFIED, has_match_records=True
        )
        assert allow is False
        assert reason == 'same_outcome'

    def test_not_qualified_to_qualified_supersedes(self):
        existing = [{'action': 'Scout Screen - Not Qualified', 'comments': '69%'}]
        allow, reason = should_supersede_existing_notes(
            existing, _NOTE_OUTCOME_QUALIFIED, has_match_records=True
        )
        assert allow is True
        assert reason.startswith('outcome_changed:')
        assert 'Not Qualified' in reason
        assert 'Qualified' in reason

    def test_qualified_to_not_qualified_supersedes(self):
        existing = [{'action': 'Scout Screen - Qualified', 'comments': '90%'}]
        allow, reason = should_supersede_existing_notes(
            existing, _NOTE_OUTCOME_NOT_QUALIFIED, has_match_records=True
        )
        assert allow is True
        assert 'outcome_changed' in reason

    def test_incomplete_still_superseded(self):
        existing = [{'action': 'Scout Screen - Incomplete', 'comments': 'Analysis failed'}]
        allow, reason = should_supersede_existing_notes(
            existing, _NOTE_OUTCOME_NOT_QUALIFIED, has_match_records=True
        )
        assert allow is True
        assert reason == 'incomplete'


class TestOutcomeChangeCreatesNote:
    """End-to-end create_candidate_note when prior outcome differs."""

    def test_creates_qualified_note_over_prior_not_qualified(self, app):
        with app.app_context():
            from candidate_vetting_service import CandidateVettingService
            from models import CandidateVettingLog, CandidateJobMatch
            from app import db
            from unittest.mock import MagicMock, patch
            from datetime import datetime

            vetting_log = CandidateVettingLog(
                bullhorn_candidate_id=45530460,
                candidate_name='Femi Regression',
                status='completed',
                is_qualified=True,
                highest_match_score=84.0,
                note_created=False,
                analyzed_at=datetime.utcnow(),
                created_at=datetime.utcnow(),
            )
            db.session.add(vetting_log)
            db.session.commit()

            match = CandidateJobMatch(
                vetting_log_id=vetting_log.id,
                bullhorn_job_id=35471,
                job_title='Senior Power BI Developer',
                match_score=84.0,
                is_qualified=True,
                is_applied_job=True,
                match_summary='Strong fit after re-score',
                skills_match='Power BI',
                experience_match='5 years',
                gaps_identified='None',
            )
            db.session.add(match)
            db.session.commit()

            service = CandidateVettingService()
            mock_bullhorn = MagicMock()
            mock_bullhorn.get_candidate_notes.return_value = [
                {
                    'id': 7213922,
                    'action': 'Scout Screen - Not Qualified',
                    'comments': 'Highest Match Score: 69%',
                }
            ]
            mock_bullhorn.create_candidate_note.return_value = 7213999

            with patch.object(service, '_get_bullhorn_service', return_value=mock_bullhorn):
                result = service.create_candidate_note(vetting_log)

            assert result is True
            mock_bullhorn.create_candidate_note.assert_called_once()
            _args, kwargs = mock_bullhorn.create_candidate_note.call_args
            # Positional: candidate_id, note_text — or kwargs action=
            note_text = _args[1] if len(_args) > 1 else kwargs.get('note_text', '')
            action = kwargs.get('action') or (_args[2] if len(_args) > 2 else None)
            assert action == 'Scout Screen - Qualified'
            assert 'UPDATED SCOUT SCREENING RESULT' in note_text
            assert 'Not Qualified' in note_text and 'Qualified' in note_text

            db.session.refresh(vetting_log)
            assert vetting_log.note_created is True
            assert vetting_log.bullhorn_note_id == 7213999

            CandidateJobMatch.query.filter_by(vetting_log_id=vetting_log.id).delete()
            CandidateVettingLog.query.filter_by(bullhorn_candidate_id=45530460).delete()
            db.session.commit()
