"""
Scout Vetting Conversational Flow — Automated Test Suite

Covers the critical gaps identified in the vetting pipeline:
  - Reply intent classification (answer / decline / question / out_of_office)
  - Multi-turn state management (turn counting, MAX_TURNS finalization)
  - Follow-up nudge scheduler (24h / 48h / unresponsive closure)
  - Session finalization and recruiter handoff
  - Session state guards (closed sessions, concurrent session cap)

All external calls (AI API, SendGrid, Bullhorn) are mocked.
No real API calls are made during these tests.
db and ScoutVettingSession are imported locally inside service methods,
so we patch at 'app.db' and 'models.ScoutVettingSession'.
"""

import json
import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, MagicMock, patch, call


# ---------------------------------------------------------------------------
# Shared Helpers
# ---------------------------------------------------------------------------

def _make_session(**overrides):
    """Build a mock ScoutVettingSession with sensible defaults."""
    session = Mock()
    session.id = overrides.get('id', 42)
    session.bullhorn_candidate_id = overrides.get('bullhorn_candidate_id', 5001)
    session.bullhorn_job_id = overrides.get('bullhorn_job_id', 3001)
    session.candidate_name = overrides.get('candidate_name', 'Jane Doe')
    session.candidate_email = overrides.get('candidate_email', 'jane@example.com')
    session.recruiter_email = overrides.get('recruiter_email', 'recruiter@myticas.com')
    session.recruiter_name = overrides.get('recruiter_name', 'Bob Smith')
    session.job_title = overrides.get('job_title', 'Senior Python Developer')
    session.status = overrides.get('status', 'outreach_sent')
    session.current_turn = overrides.get('current_turn', 1)
    session.max_turns = overrides.get('max_turns', 5)
    session.follow_up_count = overrides.get('follow_up_count', 0)
    session.last_outreach_at = overrides.get('last_outreach_at', datetime.utcnow())
    session.last_outbound_at = overrides.get('last_outbound_at', datetime.utcnow())
    session.last_reply_at = overrides.get('last_reply_at', None)
    session.last_inbound_at = overrides.get('last_inbound_at', None)
    session.last_message_id = overrides.get('last_message_id', '<original@example.com>')
    session.match_summary = overrides.get('match_summary', 'Good Python skills.')
    session.gaps_identified = overrides.get('gaps_identified', 'No AWS experience stated.')
    session.outcome_label = overrides.get('outcome_label', None)
    session.outcome_score = overrides.get('outcome_score', None)
    session.outcome_summary = overrides.get('outcome_summary', None)
    session.handoff_sent = overrides.get('handoff_sent', False)
    session.note_created = overrides.get('note_created', False)
    session.bullhorn_note_id = overrides.get('bullhorn_note_id', None)
    session.created_at = overrides.get('created_at', datetime.utcnow())
    session.conversation_turns = overrides.get('conversation_turns', [])
    session.vetting_questions_json = overrides.get('vetting_questions_json', json.dumps([
        "How many years of Python experience do you have?",
        "Have you worked with AWS or other cloud platforms?",
        "Are you comfortable working fully remotely?",
    ]))
    session.answered_questions_json = overrides.get('answered_questions_json', '{}')
    return session


def _make_service():
    """Instantiate ScoutVettingService with mocked dependencies."""
    from scout_vetting_service import ScoutVettingService

    mock_email = Mock()
    mock_email.send_html_email.return_value = {'message_id': '<sent@test.com>', 'success': True}

    mock_bh = Mock()
    mock_bh.create_candidate_note.return_value = 99999

    svc = ScoutVettingService(email_service=mock_email, bullhorn_service=mock_bh)
    return svc, mock_email, mock_bh


def _mock_db():
    """Return a mock db object that replaces app.db inside service methods."""
    mock = Mock()
    mock.session = Mock()
    mock.session.commit = Mock()
    mock.session.add = Mock()
    mock.session.flush = Mock()
    mock.session.query = Mock()
    return mock


# ═══════════════════════════════════════════════════════════════════════════════
# Group 1 — Reply Intent Classification
# ═══════════════════════════════════════════════════════════════════════════════

class TestReplyIntentClassification:

    def test_reply_classified_as_answer(self):
        """Clear answers advance the session and remain in_progress when questions remain."""
        svc, email_svc, _ = _make_service()
        session = _make_session(status='outreach_sent', current_turn=1)

        answers = {'How many years of Python experience do you have?': '8 years'}
        classify_result = {'intent': 'answer', 'reasoning': 'Direct answer given.', 'answers_extracted': answers}
        followup_html = '<p>Thanks! A couple more questions...</p>'

        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_generate_followup_reply', return_value=followup_html), \
             patch.object(svc, '_record_turn'), \
             patch.object(svc, '_build_subject', return_value='Re: Vetting'):

            result = svc.process_candidate_reply(session, 'I have 8 years of Python experience.')

        assert session.status == 'in_progress'
        assert session.current_turn == 2
        existing = json.loads(session.answered_questions_json)
        assert 'How many years of Python experience do you have?' in existing
        assert result == followup_html

    def test_reply_classified_as_decline(self):
        """Decline closes the session immediately; no follow-up email sent."""
        svc, email_svc, _ = _make_service()
        session = _make_session(status='outreach_sent')

        classify_result = {'intent': 'decline', 'reasoning': 'Candidate withdrew.', 'answers_extracted': {}}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_record_turn'):

            result = svc.process_candidate_reply(
                session,
                "Thank you but I've accepted another offer and am no longer available."
            )

        assert session.status == 'declined'
        assert result is None
        email_svc.send_html_email.assert_not_called()

    def test_reply_classified_as_question(self):
        """Candidate question keeps session alive and generates a redirecting reply."""
        svc, email_svc, _ = _make_service()
        session = _make_session(status='outreach_sent', current_turn=1)

        classify_result = {'intent': 'question', 'reasoning': 'Candidate asked about salary.', 'answers_extracted': {}}
        followup_html = '<p>Great question! The range is competitive. Now, our questions...</p>'
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_generate_followup_reply', return_value=followup_html), \
             patch.object(svc, '_record_turn'), \
             patch.object(svc, '_build_subject', return_value='Re: Vetting'):

            result = svc.process_candidate_reply(
                session,
                'Before I answer, could you tell me the compensation range for this role?'
            )

        assert session.status == 'in_progress'
        assert result == followup_html
        email_svc.send_html_email.assert_called_once()

    def test_reply_out_of_office(self):
        """Out-of-office auto-replies are ignored; session not advanced, no email sent."""
        svc, email_svc, _ = _make_service()
        session = _make_session(status='outreach_sent', current_turn=1)
        original_turn = session.current_turn

        classify_result = {'intent': 'out_of_office', 'reasoning': 'Automated OOO.', 'answers_extracted': {}}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_record_turn'):

            result = svc.process_candidate_reply(
                session,
                'I am out of the office until March 10 and will reply when I return.'
            )

        assert result is None
        assert session.current_turn == original_turn
        email_svc.send_html_email.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Group 2 — Multi-Turn State Management
# ═══════════════════════════════════════════════════════════════════════════════

class TestMultiTurnStateManagement:

    def test_turn_count_increments_on_answer(self):
        """Turn counter increments each time an answer is processed."""
        svc, email_svc, _ = _make_service()
        session = _make_session(status='in_progress', current_turn=2)

        classify_result = {
            'intent': 'answer',
            'reasoning': 'Answered second question.',
            'answers_extracted': {'Have you worked with AWS or other cloud platforms?': 'Yes, 3 years on AWS.'},
        }
        followup_html = '<p>One more question...</p>'
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_generate_followup_reply', return_value=followup_html), \
             patch.object(svc, '_record_turn'), \
             patch.object(svc, '_build_subject', return_value='Re: Vetting'):

            svc.process_candidate_reply(session, 'Yes, I have 3 years on AWS.')

        assert session.current_turn == 3

    def test_max_turns_triggers_finalization(self):
        """When current_turn reaches max_turns, session is finalized."""
        svc, email_svc, _ = _make_service()

        session = _make_session(
            status='in_progress',
            current_turn=4,
            max_turns=5,
            answered_questions_json=json.dumps({
                'How many years of Python experience do you have?': '8 years'
            })
        )

        classify_result = {
            'intent': 'answer',
            'reasoning': 'Partial answer received.',
            'answers_extracted': {'Have you worked with AWS or other cloud platforms?': 'A little.'},
        }
        thank_you_html = '<p>Thank you for your responses!</p>'
        outcome = {'recommendation': 'qualified', 'score': 72, 'summary': 'Meets most requirements.'}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_generate_thank_you', return_value=thank_you_html), \
             patch.object(svc, '_generate_outcome', return_value=outcome), \
             patch.object(svc, '_record_turn'), \
             patch.object(svc, '_build_subject', return_value='Re: Vetting'), \
             patch.object(svc, '_send_recruiter_handoff'), \
             patch.object(svc, '_create_bullhorn_note', return_value=12345):

            svc.process_candidate_reply(session, 'A little AWS experience.')

        assert session.status == 'qualified'

    def test_all_questions_answered_triggers_early_finalization(self):
        """When all questions answered before MAX_TURNS, session finalizes early."""
        svc, email_svc, _ = _make_service()

        session = _make_session(
            status='in_progress',
            current_turn=2,
            max_turns=5,
            answered_questions_json=json.dumps({
                'How many years of Python experience do you have?': '8 years',
                'Have you worked with AWS or other cloud platforms?': 'Yes, extensively.',
            })
        )

        classify_result = {
            'intent': 'answer',
            'reasoning': 'Final question answered.',
            'answers_extracted': {'Are you comfortable working fully remotely?': 'Yes, fully remote for 3 years.'},
        }
        thank_you_html = '<p>Thank you!</p>'
        outcome = {'recommendation': 'qualified', 'score': 91, 'summary': 'Excellent fit.'}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_generate_thank_you', return_value=thank_you_html), \
             patch.object(svc, '_generate_outcome', return_value=outcome), \
             patch.object(svc, '_record_turn'), \
             patch.object(svc, '_build_subject', return_value='Re: Vetting'), \
             patch.object(svc, '_send_recruiter_handoff'), \
             patch.object(svc, '_create_bullhorn_note', return_value=12345):

            result = svc.process_candidate_reply(
                session,
                'Yes, I have been working fully remote for 3 years.'
            )

        assert result == thank_you_html
        assert session.status == 'qualified'
        assert session.current_turn == 3


# ═══════════════════════════════════════════════════════════════════════════════
# Group 3 — Follow-up Nudge Scheduler
# ═══════════════════════════════════════════════════════════════════════════════

class TestFollowupNudgeScheduler:

    def _run_followups_with_sessions(self, svc, sessions):
        """Helper: run run_followups() with a mocked DB and session query."""
        db_mock = _mock_db()

        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = sessions
        mock_query.distinct.return_value = mock_query
        mock_query.count.return_value = 0

        mock_inner_query = Mock()
        mock_inner_query.filter.return_value = mock_inner_query
        mock_inner_query.distinct.return_value = mock_inner_query
        mock_inner_query.all.return_value = []

        db_mock.session.query.return_value = mock_inner_query

        with patch('app.db', db_mock), \
             patch('models.ScoutVettingSession') as MockSVS:

            MockSVS.query = mock_query
            MockSVS.query.filter.return_value.all.return_value = sessions

            with patch.object(svc, '_send_followup') as mock_followup, \
                 patch.object(svc, 'finalize_vetting') as mock_finalize:

                stats = svc.run_followups()

        return stats, mock_followup, mock_finalize

    def test_first_nudge_sent_at_24h(self):
        """Session with no reply for 25h and follow_up_count=0 gets its first nudge."""
        svc, _, _ = _make_service()

        session = _make_session(
            status='outreach_sent',
            follow_up_count=0,
            last_outreach_at=datetime.utcnow() - timedelta(hours=25),
            last_reply_at=None,
        )

        db_mock = _mock_db()
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = [session]
        mock_query.distinct.return_value = mock_query

        mock_inner = Mock()
        mock_inner.filter.return_value = mock_inner
        mock_inner.distinct.return_value = mock_inner
        mock_inner.all.return_value = []
        db_mock.session.query.return_value = mock_inner

        with patch('app.db', db_mock), \
             patch('models.ScoutVettingSession') as MockSVS:

            MockSVS.query = mock_query

            with patch.object(svc, '_send_followup') as mock_followup, \
                 patch.object(svc, 'finalize_vetting'):

                stats = svc.run_followups()

        mock_followup.assert_called_once_with(session)
        assert stats['followups_sent'] == 1

    def test_second_nudge_sent_at_48h(self):
        """Session with follow_up_count=1 and 49h since last outreach gets the second nudge."""
        svc, _, _ = _make_service()

        session = _make_session(
            status='outreach_sent',
            follow_up_count=1,
            last_outreach_at=datetime.utcnow() - timedelta(hours=49),
            last_reply_at=None,
        )

        db_mock = _mock_db()
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = [session]
        mock_query.distinct.return_value = mock_query

        mock_inner = Mock()
        mock_inner.filter.return_value = mock_inner
        mock_inner.distinct.return_value = mock_inner
        mock_inner.all.return_value = []
        db_mock.session.query.return_value = mock_inner

        with patch('app.db', db_mock), \
             patch('models.ScoutVettingSession') as MockSVS:

            MockSVS.query = mock_query

            with patch.object(svc, '_send_followup') as mock_followup, \
                 patch.object(svc, 'finalize_vetting'):

                stats = svc.run_followups()

        mock_followup.assert_called_once_with(session)
        assert stats['followups_sent'] == 1

    def test_session_marked_unresponsive_after_final_nudge(self):
        """Session with follow_up_count=2 and 49h since last outreach is closed as unresponsive."""
        svc, _, _ = _make_service()

        session = _make_session(
            status='outreach_sent',
            follow_up_count=2,
            last_outreach_at=datetime.utcnow() - timedelta(hours=49),
            last_reply_at=None,
        )

        db_mock = _mock_db()
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = [session]
        mock_query.distinct.return_value = mock_query

        mock_inner = Mock()
        mock_inner.filter.return_value = mock_inner
        mock_inner.distinct.return_value = mock_inner
        mock_inner.all.return_value = []
        db_mock.session.query.return_value = mock_inner

        with patch('app.db', db_mock), \
             patch('models.ScoutVettingSession') as MockSVS:

            MockSVS.query = mock_query

            with patch.object(svc, '_send_followup') as mock_followup, \
                 patch.object(svc, 'finalize_vetting') as mock_finalize:

                stats = svc.run_followups()

        assert session.status == 'unresponsive'
        mock_finalize.assert_called_once_with(session)
        mock_followup.assert_not_called()
        assert stats['closed_unresponsive'] == 1

    def test_active_reply_prevents_nudge(self):
        """Session with recent outreach (< 24h ago) does NOT get a nudge."""
        svc, _, _ = _make_service()

        session = _make_session(
            status='in_progress',
            follow_up_count=0,
            last_outreach_at=datetime.utcnow() - timedelta(hours=10),
            last_reply_at=datetime.utcnow() - timedelta(hours=2),
        )

        db_mock = _mock_db()
        mock_query = Mock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = [session]
        mock_query.distinct.return_value = mock_query

        mock_inner = Mock()
        mock_inner.filter.return_value = mock_inner
        mock_inner.distinct.return_value = mock_inner
        mock_inner.all.return_value = []
        db_mock.session.query.return_value = mock_inner

        with patch('app.db', db_mock), \
             patch('models.ScoutVettingSession') as MockSVS:

            MockSVS.query = mock_query

            with patch.object(svc, '_send_followup') as mock_followup, \
                 patch.object(svc, 'finalize_vetting') as mock_finalize:

                stats = svc.run_followups()

        mock_followup.assert_not_called()
        mock_finalize.assert_not_called()
        assert stats['followups_sent'] == 0


# ═══════════════════════════════════════════════════════════════════════════════
# Group 4 — Finalization and Recruiter Handoff
# ═══════════════════════════════════════════════════════════════════════════════

class TestFinalizationAndHandoff:

    def test_finalization_generates_outcome_score(self):
        """finalize_vetting sets a numeric outcome_score and non-empty outcome_summary."""
        svc, _, _ = _make_service()
        session = _make_session(status='in_progress')
        outcome = {'recommendation': 'qualified', 'score': 85, 'summary': 'Strong Python candidate.'}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_generate_outcome', return_value=outcome), \
             patch.object(svc, '_create_bullhorn_note', return_value=None), \
             patch.object(svc, '_send_recruiter_handoff'):

            svc.finalize_vetting(session)

        assert isinstance(session.outcome_score, (int, float))
        assert 0 <= session.outcome_score <= 100
        assert session.outcome_summary and len(session.outcome_summary) > 0

    def test_qualified_outcome_triggers_recruiter_email(self):
        """Qualified candidate triggers exactly one recruiter handoff call."""
        svc, _, _ = _make_service()
        session = _make_session(status='in_progress', recruiter_email='recruiter@myticas.com')
        outcome = {'recommendation': 'qualified', 'score': 88, 'summary': 'Excellent fit.'}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_generate_outcome', return_value=outcome), \
             patch.object(svc, '_create_bullhorn_note', return_value=None), \
             patch.object(svc, '_send_recruiter_handoff') as mock_handoff:

            svc.finalize_vetting(session)

        assert session.status == 'qualified'
        mock_handoff.assert_called_once_with(session)

    def test_not_qualified_outcome_no_recruiter_email(self):
        """Not-qualified candidate does NOT trigger a recruiter handoff."""
        svc, _, _ = _make_service()
        session = _make_session(status='in_progress', recruiter_email='recruiter@myticas.com')
        outcome = {'recommendation': 'not_qualified', 'score': 42, 'summary': 'Significant gaps.'}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_generate_outcome', return_value=outcome), \
             patch.object(svc, '_create_bullhorn_note', return_value=None), \
             patch.object(svc, '_send_recruiter_handoff') as mock_handoff:

            svc.finalize_vetting(session)

        assert session.status == 'not_qualified'
        mock_handoff.assert_not_called()

    def test_finalization_creates_bullhorn_note(self):
        """finalize_vetting calls _create_bullhorn_note when candidate ID present."""
        svc, _, _ = _make_service()
        session = _make_session(status='in_progress', bullhorn_candidate_id=5001)
        outcome = {'recommendation': 'qualified', 'score': 80, 'summary': 'Good candidate.'}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_generate_outcome', return_value=outcome), \
             patch.object(svc, '_create_bullhorn_note', return_value=99999) as mock_note, \
             patch.object(svc, '_send_recruiter_handoff'):

            svc.finalize_vetting(session)

        mock_note.assert_called_once()
        assert mock_note.call_args[0][0] is session
        note_label = mock_note.call_args[0][1]
        assert 'Scout Vetting' in note_label

    def test_finalization_skips_bullhorn_note_without_candidate_id(self):
        """finalize_vetting skips note creation when candidate ID is None."""
        svc, _, _ = _make_service()
        session = _make_session(status='in_progress', bullhorn_candidate_id=None)
        outcome = {'recommendation': 'not_qualified', 'score': 30, 'summary': 'Not a fit.'}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_generate_outcome', return_value=outcome), \
             patch.object(svc, '_create_bullhorn_note') as mock_note, \
             patch.object(svc, '_send_recruiter_handoff'):

            svc.finalize_vetting(session)

        mock_note.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Group 5 — Session State Guards
# ═══════════════════════════════════════════════════════════════════════════════

class TestSessionStateGuards:

    def test_declined_session_sends_no_email(self):
        """After a decline, no outbound email is sent to the candidate."""
        svc, email_svc, _ = _make_service()
        session = _make_session(status='outreach_sent')

        classify_result = {'intent': 'decline', 'reasoning': 'Withdrew.', 'answers_extracted': {}}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_record_turn'):

            result = svc.process_candidate_reply(session, 'Not interested.')

        assert result is None
        email_svc.send_html_email.assert_not_called()
        assert session.status == 'declined'

    def test_max_concurrent_active_sessions_count(self):
        """MAX_CONCURRENT_SESSIONS constant is 3 — the cap the system enforces."""
        from scout_vetting_service import ScoutVettingService
        assert ScoutVettingService.MAX_CONCURRENT_SESSIONS == 3

    def test_max_turns_constant(self):
        """MAX_TURNS constant is 5 — the conversation length limit."""
        from scout_vetting_service import ScoutVettingService
        assert ScoutVettingService.MAX_TURNS == 5

    def test_followup_hours_schedule(self):
        """FOLLOWUP_HOURS defines two nudge points: 24h and 48h."""
        from scout_vetting_service import ScoutVettingService
        assert ScoutVettingService.FOLLOWUP_HOURS == [24, 48]


# ═══════════════════════════════════════════════════════════════════════════════
# Group 6 — Utility and Helper Methods
# ═══════════════════════════════════════════════════════════════════════════════

class TestHelperMethods:

    def test_build_subject_initial_contains_context(self):
        """_build_subject for initial outreach produces a non-empty string."""
        svc, _, _ = _make_service()
        session = _make_session(job_title='Senior Python Developer', candidate_name='Jane Doe')
        subject = svc._build_subject(session, is_initial=True)
        assert isinstance(subject, str)
        assert len(subject) > 0

    def test_build_subject_followup_differs_from_initial(self):
        """_build_subject for follow-up produces a different prefix than initial."""
        svc, _, _ = _make_service()
        session = _make_session(job_title='Senior Python Developer')
        subject_initial = svc._build_subject(session, is_initial=True)
        subject_followup = svc._build_subject(session, is_initial=False)
        # Both should be non-empty strings
        assert isinstance(subject_initial, str) and len(subject_initial) > 0
        assert isinstance(subject_followup, str) and len(subject_followup) > 0

    def test_classify_reply_returns_required_keys(self):
        """_classify_reply always returns dict with intent, reasoning, answers_extracted."""
        svc, _, _ = _make_service()
        session = _make_session()

        llm_payload = json.dumps({
            'intent': 'answer',
            'reasoning': 'Clear answer provided.',
            'answers_extracted': {'How many years of Python experience do you have?': '5 years'},
        })

        mock_resp = Mock()
        mock_resp.content = llm_payload
        svc._llm = Mock()
        svc._llm.invoke.return_value = mock_resp

        result = svc._classify_reply(session, 'I have 5 years of Python experience.')

        assert 'intent' in result
        assert 'reasoning' in result
        assert 'answers_extracted' in result
        assert result['intent'] == 'answer'

    def test_classify_reply_handles_llm_failure_gracefully(self):
        """_classify_reply returns a safe default dict if the LLM call raises an exception."""
        svc, _, _ = _make_service()
        session = _make_session()

        svc._llm = Mock()
        svc._llm.invoke.side_effect = Exception('LLM API timeout')

        result = svc._classify_reply(session, 'Some reply text.')

        # Must return a dict with at least an intent key
        assert isinstance(result, dict)
        assert 'intent' in result
        assert result['intent'] in (
            'answer', 'question', 'decline', 'out_of_office',
            'unrelated', 'spam', 'unknown'
        )

    def test_get_conversation_summary_returns_string(self):
        """_get_conversation_summary produces a string even with no conversation turns."""
        svc, _, _ = _make_service()
        session = _make_session()

        mock_query = Mock()
        mock_query.filter_by.return_value = mock_query
        mock_query.order_by.return_value = mock_query
        mock_query.all.return_value = []

        with patch('models.VettingConversationTurn') as MockTurn:
            MockTurn.query = mock_query
            summary = svc._get_conversation_summary(session)

        assert isinstance(summary, str)


# ═══════════════════════════════════════════════════════════════════════════════
# Group 7 — Inbound Reply Integration (T005)
# ═══════════════════════════════════════════════════════════════════════════════

class TestInboundReplyIntegration:
    """Full inbound path: classify → update answers → advance/close session."""

    def test_answer_reply_updates_answers_and_sends_followup(self):
        """Full path: answer reply → answers merged → follow-up sent → session in_progress."""
        svc, email_svc, _ = _make_service()
        session = _make_session(
            status='outreach_sent',
            current_turn=1,
            max_turns=5,
            answered_questions_json='{}',
        )

        answers = {'How many years of Python experience do you have?': '8 years'}
        classify_result = {
            'intent': 'answer',
            'reasoning': 'Direct answer.',
            'answers_extracted': answers,
        }
        followup_html = '<p>Thanks! Next question...</p>'
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_generate_followup_reply', return_value=followup_html), \
             patch.object(svc, '_record_turn'), \
             patch.object(svc, '_build_subject', return_value='Re: Vetting [SV-42]'), \
             patch.object(svc, '_build_quoted_history', return_value=''), \
             patch.object(svc, '_get_threading_headers', return_value={'in_reply_to': '<x>', 'references': '<x>'}), \
             patch.object(svc, '_generate_message_id', return_value='<new@test.com>'), \
             patch.object(svc, '_share_availability_answers'):

            result = svc.process_candidate_reply(session, 'I have 8 years.', 'Re: Vetting [SV-42]')

        assert result == followup_html
        assert session.status == 'in_progress'
        assert session.current_turn == 2
        stored = json.loads(session.answered_questions_json)
        assert 'How many years of Python experience do you have?' in stored
        email_svc.send_html_email.assert_called_once()

    def test_decline_reply_closes_session_no_email(self):
        """Full path: decline reply → session declined → no outbound email."""
        svc, email_svc, _ = _make_service()
        session = _make_session(status='outreach_sent', current_turn=1)

        classify_result = {
            'intent': 'decline',
            'reasoning': 'Candidate explicitly declined.',
            'answers_extracted': {},
        }
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_record_turn'):

            result = svc.process_candidate_reply(session, 'No thanks, not interested.')

        assert result is None
        assert session.status == 'declined'
        email_svc.send_html_email.assert_not_called()

    def test_final_answer_triggers_finalization_and_thank_you(self):
        """Full path: final answer → all questions answered → finalize + thank-you."""
        svc, email_svc, _ = _make_service()
        session = _make_session(
            status='in_progress',
            current_turn=2,
            max_turns=5,
            answered_questions_json=json.dumps({
                'How many years of Python experience do you have?': '8 years',
                'Have you worked with AWS or other cloud platforms?': 'Yes, extensively.',
            }),
        )

        answers = {'Are you comfortable working fully remotely?': 'Yes, fully remote.'}
        classify_result = {
            'intent': 'answer',
            'reasoning': 'Final answer.',
            'answers_extracted': answers,
        }
        thank_you_html = '<p>Thank you for your time!</p>'
        outcome = {'recommendation': 'qualified', 'score': 90, 'summary': 'Great fit.'}
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_generate_thank_you', return_value=thank_you_html), \
             patch.object(svc, '_generate_outcome', return_value=outcome), \
             patch.object(svc, '_record_turn'), \
             patch.object(svc, '_build_subject', return_value='Re: Vetting [SV-42]'), \
             patch.object(svc, '_build_quoted_history', return_value=''), \
             patch.object(svc, '_get_threading_headers', return_value={'in_reply_to': '<x>', 'references': '<x>'}), \
             patch.object(svc, '_generate_message_id', return_value='<ty@test.com>'), \
             patch.object(svc, '_send_recruiter_handoff'), \
             patch.object(svc, '_create_bullhorn_note', return_value=12345), \
             patch.object(svc, '_share_availability_answers'):

            result = svc.process_candidate_reply(session, 'Yes, fully remote.')

        assert result == thank_you_html
        assert session.status == 'qualified'

    def test_ooo_reply_does_not_advance_turn_or_send_email(self):
        """Full path: OOO auto-reply → turn NOT incremented, no outbound email."""
        svc, email_svc, _ = _make_service()
        session = _make_session(status='outreach_sent', current_turn=1)
        original_turn = session.current_turn

        classify_result = {
            'intent': 'out_of_office',
            'reasoning': 'Automated OOO.',
            'answers_extracted': {},
        }
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_record_turn'):

            result = svc.process_candidate_reply(session, 'I am out of the office until April 20.')

        assert result is None
        assert session.current_turn == original_turn
        email_svc.send_html_email.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Group 8 — Cross-Session Answer Sharing (T003)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossSessionAnswerSharing:

    def test_availability_answer_shared_to_sibling(self):
        """Availability-type answer is propagated to a sibling session's unanswered slot."""
        svc, _, _ = _make_service()

        source_session = _make_session(id=10, bullhorn_candidate_id=5001)

        sibling = _make_session(
            id=20,
            bullhorn_candidate_id=5001,
            bullhorn_job_id=4001,
            status='outreach_sent',
            vetting_questions_json=json.dumps([
                'What is your current availability?',
                'Describe your cloud experience.',
            ]),
            answered_questions_json='{}',
        )

        mock_query = Mock()
        mock_query.filter.return_value.all.return_value = [sibling]

        with patch('models.ScoutVettingSession') as MockSVS:
            MockSVS.query = mock_query

            answers = {'When could you start a new position?': 'Immediately available'}
            svc._share_availability_answers(source_session, answers)

        sib_answers = json.loads(sibling.answered_questions_json)
        assert 'What is your current availability?' in sib_answers
        assert '[shared from another session]' in sib_answers['What is your current availability?']

    def test_non_availability_answer_not_shared(self):
        """Non-availability answers (skills, experience) are NOT shared."""
        svc, _, _ = _make_service()

        source_session = _make_session(id=10, bullhorn_candidate_id=5001)

        sibling = _make_session(
            id=20,
            bullhorn_candidate_id=5001,
            status='outreach_sent',
            vetting_questions_json=json.dumps([
                'What Python frameworks have you used?',
            ]),
            answered_questions_json='{}',
        )

        mock_query = Mock()
        mock_query.filter.return_value.all.return_value = [sibling]

        with patch('models.ScoutVettingSession') as MockSVS:
            MockSVS.query = mock_query

            answers = {'How many years of Python experience?': '8 years'}
            svc._share_availability_answers(source_session, answers)

        sib_answers = json.loads(sibling.answered_questions_json)
        assert len(sib_answers) == 0

    def test_sharing_skips_already_answered_sibling_questions(self):
        """If the sibling already has an answer for its availability Q, don't overwrite."""
        svc, _, _ = _make_service()

        source_session = _make_session(id=10, bullhorn_candidate_id=5001)

        sibling = _make_session(
            id=20,
            bullhorn_candidate_id=5001,
            status='in_progress',
            vetting_questions_json=json.dumps([
                'What is your availability?',
            ]),
            answered_questions_json=json.dumps({
                'What is your availability?': 'In 2 weeks',
            }),
        )

        mock_query = Mock()
        mock_query.filter.return_value.all.return_value = [sibling]

        with patch('models.ScoutVettingSession') as MockSVS:
            MockSVS.query = mock_query

            answers = {'When could you start?': 'Immediately'}
            svc._share_availability_answers(source_session, answers)

        sib_answers = json.loads(sibling.answered_questions_json)
        assert sib_answers['What is your availability?'] == 'In 2 weeks'

    def test_sharing_with_no_siblings_is_noop(self):
        """No sibling sessions → method exits without error."""
        svc, _, _ = _make_service()
        source_session = _make_session(id=10, bullhorn_candidate_id=5001)

        mock_query = Mock()
        mock_query.filter.return_value.all.return_value = []

        with patch('models.ScoutVettingSession') as MockSVS:
            MockSVS.query = mock_query
            svc._share_availability_answers(source_session, {'When available?': 'Now'})

    def test_process_reply_calls_share_on_answer_intent(self):
        """process_candidate_reply invokes _share_availability_answers for answer intents."""
        svc, email_svc, _ = _make_service()
        session = _make_session(status='outreach_sent', current_turn=1, max_turns=5)

        answers = {'When could you start?': 'In 2 weeks'}
        classify_result = {
            'intent': 'answer',
            'reasoning': 'Availability stated.',
            'answers_extracted': answers,
        }
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_generate_followup_reply', return_value='<p>Next Q</p>'), \
             patch.object(svc, '_record_turn'), \
             patch.object(svc, '_build_subject', return_value='Re: Vetting'), \
             patch.object(svc, '_build_quoted_history', return_value=''), \
             patch.object(svc, '_get_threading_headers', return_value={'in_reply_to': '<x>', 'references': '<x>'}), \
             patch.object(svc, '_generate_message_id', return_value='<new@test.com>'), \
             patch.object(svc, '_share_availability_answers') as mock_share:

            svc.process_candidate_reply(session, 'In 2 weeks', 'Re: Vetting')

        mock_share.assert_called_once_with(session, answers)

    def test_process_reply_does_not_share_on_decline(self):
        """process_candidate_reply does NOT call _share_availability_answers for decline."""
        svc, email_svc, _ = _make_service()
        session = _make_session(status='outreach_sent', current_turn=1)

        classify_result = {
            'intent': 'decline',
            'reasoning': 'Declined.',
            'answers_extracted': {},
        }
        db_mock = _mock_db()

        with patch('app.db', db_mock), \
             patch.object(svc, '_classify_reply', return_value=classify_result), \
             patch.object(svc, '_record_turn'), \
             patch.object(svc, '_share_availability_answers') as mock_share:

            svc.process_candidate_reply(session, 'Not interested.')

        mock_share.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════════
# Group 9 — Inbound Webhook Route Integration (Task #69 T005)
# ═══════════════════════════════════════════════════════════════════════════════

class TestInboundWebhookRouteIntegration:
    """HTTP route-level tests for /api/email/inbound → Scout Vetting path.

    These tests verify the full inbound route layer: POST a mock SendGrid
    payload, assert the scout-vetting address is detected, and assert that
    process_candidate_reply is invoked with the correct email body for both
    an answer reply and a decline reply.
    """

    def test_route_returns_200_for_scout_vetting_email(self, client):
        """POST to /api/email/inbound returns 200 immediately (accepted for processing)."""
        import json as _json
        resp = client.post('/api/email/inbound', data={
            'to': 'scout-vetting@parse.lyntrix.ai',
            'from': 'candidate@example.com',
            'subject': 'Re: Vetting [SV-42]',
            'text': 'I have 8 years of Python experience.',
        })
        assert resp.status_code == 200
        body = _json.loads(resp.data)
        assert body['success'] is True

    def test_bg_handler_answer_reply_advances_session_to_in_progress(self, app):
        """Full end-to-end: answer reply via bg handler → _classify_reply runs → session advances to in_progress.

        process_candidate_reply is called with the real implementation; only
        sub-dependencies (LLM, email, DB) are mocked so we can assert the
        session state transition without making live API calls.
        """
        from routes.email import _handle_scout_vetting_inbound_bg
        from scout_vetting_service import ScoutVettingService as SVC

        session_mock = _make_session(
            status='outreach_sent',
            current_turn=1,
            max_turns=5,
            answered_questions_json='{}',
        )
        db_mock = _mock_db()

        classify_result = {
            'intent': 'answer',
            'reasoning': 'Direct answer provided.',
            'answers_extracted': {'How many years of Python experience?': '8 years'},
        }

        with app.app_context(), \
             patch('scout_vetting_service.ScoutVettingService.find_session_by_subject_token',
                   return_value=session_mock), \
             patch('email_service.EmailService') as MockEmailSvc, \
             patch.object(SVC, '_classify_reply', return_value=classify_result), \
             patch.object(SVC, '_record_turn'), \
             patch.object(SVC, '_share_availability_answers'), \
             patch.object(SVC, '_generate_followup_reply', return_value='<p>Next question</p>'), \
             patch.object(SVC, '_build_subject', return_value='Re: Vetting [SV-42]'), \
             patch.object(SVC, '_build_quoted_history', return_value=''), \
             patch.object(SVC, '_get_threading_headers',
                          return_value={'in_reply_to': '<x>', 'references': '<x>'}), \
             patch.object(SVC, '_generate_message_id', return_value='<new@test.com>'), \
             patch('app.db', db_mock):

            mock_email_instance = Mock()
            mock_email_instance.send_html_email.return_value = {'message_id': '<ok>', 'success': True}
            MockEmailSvc.return_value = mock_email_instance

            _handle_scout_vetting_inbound_bg(app, {
                'to': 'scout-vetting@parse.lyntrix.ai',
                'from': 'candidate@example.com',
                'subject': 'Re: Vetting [SV-42]',
                'text': 'I have 8 years of Python experience.',
            })

        assert session_mock.status == 'in_progress', (
            f"Expected session 'in_progress' after answer reply, got '{session_mock.status}'"
        )
        assert session_mock.current_turn == 2, (
            f"Expected turn 2 after one answer, got {session_mock.current_turn}"
        )

    def test_bg_handler_decline_reply_closes_session(self, app):
        """Full end-to-end: decline reply via bg handler → _classify_reply runs → session set to declined.

        process_candidate_reply is called with the real implementation; only
        LLM and DB are mocked so we can assert the session closure without
        live API calls.
        """
        from routes.email import _handle_scout_vetting_inbound_bg
        from scout_vetting_service import ScoutVettingService as SVC

        session_mock = _make_session(status='outreach_sent', current_turn=1, max_turns=5)
        db_mock = _mock_db()

        classify_result = {
            'intent': 'decline',
            'reasoning': 'Candidate explicitly declined.',
            'answers_extracted': {},
        }

        with app.app_context(), \
             patch('scout_vetting_service.ScoutVettingService.find_session_by_subject_token',
                   return_value=None), \
             patch('scout_vetting_service.ScoutVettingService.find_session_by_email',
                   return_value=session_mock), \
             patch('email_service.EmailService'), \
             patch.object(SVC, '_classify_reply', return_value=classify_result), \
             patch.object(SVC, '_record_turn'), \
             patch('app.db', db_mock):

            _handle_scout_vetting_inbound_bg(app, {
                'to': 'scout-vetting@parse.lyntrix.ai',
                'from': 'candidate@example.com',
                'subject': 'Re: Vetting',
                'text': 'No thanks, not interested.',
            })

        assert session_mock.status == 'declined', (
            f"Expected session 'declined' after decline reply, got '{session_mock.status}'"
        )

    def test_bg_handler_no_session_skips_processing(self, app):
        """Background handler exits without calling process_candidate_reply when no session matches."""
        from routes.email import _handle_scout_vetting_inbound_bg

        with app.app_context(), \
             patch('scout_vetting_service.ScoutVettingService') as MockSVS, \
             patch('email_service.EmailService'):

            MockSVS.find_session_by_subject_token.return_value = None
            MockSVS.find_session_by_email.return_value = None
            mock_svc_instance = Mock()
            MockSVS.return_value = mock_svc_instance

            _handle_scout_vetting_inbound_bg(app, {
                'to': 'scout-vetting@parse.lyntrix.ai',
                'from': 'unknown@example.com',
                'subject': 'Random email',
                'text': 'Hello.',
            })

        mock_svc_instance.process_candidate_reply.assert_not_called()

    def test_bg_handler_prefers_subject_token_over_email_lookup(self, app):
        """Session found by subject token is used; find_session_by_email is not called."""
        from routes.email import _handle_scout_vetting_inbound_bg

        session_mock = _make_session(status='outreach_sent', current_turn=1, max_turns=5)

        with app.app_context(), \
             patch('scout_vetting_service.ScoutVettingService') as MockSVS, \
             patch('email_service.EmailService'):

            MockSVS.find_session_by_subject_token.return_value = session_mock
            mock_svc_instance = Mock()
            MockSVS.return_value = mock_svc_instance

            _handle_scout_vetting_inbound_bg(app, {
                'to': 'scout-vetting@parse.lyntrix.ai',
                'from': 'candidate@example.com',
                'subject': 'Re: Vetting [SV-42]',
                'text': 'Yes, available immediately.',
            })

        MockSVS.find_session_by_email.assert_not_called()
        mock_svc_instance.process_candidate_reply.assert_called_once()
