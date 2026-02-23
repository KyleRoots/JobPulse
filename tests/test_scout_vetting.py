"""
Scout Vetting Service unit tests for JobPulse.

Tests ScoutVettingService methods with mocked Claude, SendGrid, and
database dependencies.  These tests exercise the service in isolation
without actual API calls.
"""

import json
import pytest
from unittest.mock import Mock, patch, PropertyMock
from datetime import datetime, timedelta


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session(**overrides):
    """Create a mock ScoutVettingSession with correct field names."""
    session = Mock()
    session.id = overrides.get('id', 42)
    session.tenant_id = overrides.get('tenant_id', 1)
    session.vetting_log_id = overrides.get('vetting_log_id', 100)
    session.match_id = overrides.get('match_id', 200)
    session.candidate_job_match_id = overrides.get('candidate_job_match_id', 200)
    session.bullhorn_candidate_id = overrides.get('bullhorn_candidate_id', 5001)
    session.bullhorn_job_id = overrides.get('bullhorn_job_id', 3001)
    session.candidate_name = overrides.get('candidate_name', 'Jane Doe')
    session.candidate_email = overrides.get('candidate_email', 'jane@example.com')
    session.job_title = overrides.get('job_title', 'Software Engineer')
    session.recruiter_email = overrides.get('recruiter_email', 'recruiter@example.com')
    session.recruiter_name = overrides.get('recruiter_name', 'Bob Recruiter')
    session.status = overrides.get('status', 'outreach_sent')
    session.current_turn = overrides.get('current_turn', 1)
    session.max_turns = overrides.get('max_turns', 5)
    # These field names match the actual model
    session.vetting_questions_json = overrides.get('vetting_questions_json', json.dumps([
        "What is your experience with Python?",
        "Describe a project you led.",
        "Are you comfortable with remote work?"
    ]))
    session.answered_questions_json = overrides.get('answered_questions_json', '{}')
    session.follow_up_count = overrides.get('follow_up_count', 0)
    session.last_outbound_at = overrides.get('last_outbound_at', datetime.utcnow())
    session.last_outreach_at = overrides.get('last_outreach_at', datetime.utcnow())
    session.last_inbound_at = overrides.get('last_inbound_at', None)
    session.last_reply_at = overrides.get('last_reply_at', None)
    session.match_summary = overrides.get('match_summary', 'Strong skills match')
    session.gaps_identified = overrides.get('gaps_identified', 'No cloud experience')
    session.outcome_label = overrides.get('outcome_label', None)
    session.outcome_score = overrides.get('outcome_score', None)
    session.outcome_summary = overrides.get('outcome_summary', None)
    session.last_message_id = overrides.get('last_message_id', '<msg1@example.com>')
    session.created_at = overrides.get('created_at', datetime.utcnow())
    session.updated_at = overrides.get('updated_at', datetime.utcnow())
    session.conversation_turns = overrides.get('conversation_turns', [])
    return session


def _make_match(**overrides):
    """Create a mock CandidateJobMatch."""
    match = Mock()
    match.id = overrides.get('id', 200)
    match.bullhorn_job_id = overrides.get('bullhorn_job_id', 3001)
    match.job_title = overrides.get('job_title', 'Software Engineer')
    match.match_score = overrides.get('match_score', 85.0)
    match.match_summary = overrides.get('match_summary', 'Strong skills match')
    match.gaps_identified = overrides.get('gaps_identified', 'No cloud experience')
    match.is_qualified = overrides.get('is_qualified', True)
    match.recruiter_email = overrides.get('recruiter_email', 'recruiter@example.com')
    match.recruiter_name = overrides.get('recruiter_name', 'Bob Recruiter')
    return match


def _make_vetting_log(**overrides):
    """Create a mock CandidateVettingLog."""
    log = Mock()
    log.id = overrides.get('id', 100)
    log.tenant_id = overrides.get('tenant_id', 1)
    log.bullhorn_candidate_id = overrides.get('bullhorn_candidate_id', 5001)
    log.candidate_name = overrides.get('candidate_name', 'Jane Doe')
    log.candidate_email = overrides.get('candidate_email', 'jane@example.com')
    log.created_at = overrides.get('created_at', datetime.utcnow())
    log.analyzed_at = overrides.get('analyzed_at', datetime.utcnow())
    return log


# ===========================================================================
# Pure unit tests (no app context needed)
# ===========================================================================

class TestScoutVettingServiceInit:
    """Test service initialization."""

    def test_init_without_email_service(self):
        from scout_vetting_service import ScoutVettingService
        svc = ScoutVettingService(email_service=None)
        assert svc is not None

    def test_init_with_email_service(self):
        from scout_vetting_service import ScoutVettingService
        mock_email = Mock()
        svc = ScoutVettingService(email_service=mock_email)
        assert svc.email_service is mock_email


class TestBuildSubject:
    """Test _build_subject method."""

    def test_initial_subject_contains_token(self):
        from scout_vetting_service import ScoutVettingService
        svc = ScoutVettingService(email_service=None)
        session = _make_session(id=42, job_title='Data Analyst')

        subject = svc._build_subject(session, is_initial=True)
        assert '[SV-42]' in subject
        assert 'Data Analyst' in subject

    def test_followup_subject_has_re_prefix(self):
        from scout_vetting_service import ScoutVettingService
        svc = ScoutVettingService(email_service=None)
        session = _make_session(id=42, job_title='Data Analyst')

        subject = svc._build_subject(session, is_initial=False)
        assert subject.startswith('Re:')
        assert '[SV-42]' in subject


class TestEmailBuilding:
    """Test email HTML builders."""

    def test_outreach_email_contains_questions(self):
        from scout_vetting_service import ScoutVettingService
        svc = ScoutVettingService(email_service=None)
        session = _make_session(candidate_name='Jane Doe')
        questions = ['What is your Python experience?', 'Are you available?']

        html = svc._build_outreach_email(session, questions)
        assert 'Python experience' in html
        assert 'Jane' in html

    def test_followup_email_contains_name(self):
        from scout_vetting_service import ScoutVettingService
        svc = ScoutVettingService(email_service=None)
        session = _make_session(candidate_name='Jane Doe')

        html = svc._build_followup_email(session, follow_up_num=1, is_final=False)
        assert 'Jane' in html

    def test_followup_final_different_from_first(self):
        from scout_vetting_service import ScoutVettingService
        svc = ScoutVettingService(email_service=None)
        session = _make_session(candidate_name='Jane Doe')

        html_first = svc._build_followup_email(session, follow_up_num=1, is_final=False)
        html_final = svc._build_followup_email(session, follow_up_num=2, is_final=True)
        assert html_first != html_final


class TestFormatDuration:
    """Test _format_duration static method."""

    def test_format_short_duration(self):
        from scout_vetting_service import ScoutVettingService
        session = _make_session(
            created_at=datetime.utcnow() - timedelta(minutes=30),
            updated_at=datetime.utcnow()
        )
        result = ScoutVettingService._format_duration(session)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_format_long_duration(self):
        from scout_vetting_service import ScoutVettingService
        session = _make_session(
            created_at=datetime.utcnow() - timedelta(days=3, hours=2),
            updated_at=datetime.utcnow()
        )
        result = ScoutVettingService._format_duration(session)
        assert 'day' in result.lower() or '3' in result


# ===========================================================================
# Tests requiring app context
# ===========================================================================

class TestSubjectTokenParsing:
    """Test find_session_by_subject_token (uses .query.get())."""

    def test_extracts_session_id(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from models import ScoutVettingSession

            mock_session = _make_session(id=42)
            with patch.object(ScoutVettingSession, 'query') as mock_query:
                mock_query.get.return_value = mock_session
                result = ScoutVettingService.find_session_by_subject_token(
                    'Re: Quick follow-up — Software Engineer [SV-42]'
                )
                assert result is mock_session
                mock_query.get.assert_called_once_with(42)

    def test_returns_none_without_token(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            result = ScoutVettingService.find_session_by_subject_token(
                'Re: Hello, just a normal email'
            )
            assert result is None


class TestFindSessionByEmail:
    """Test find_session_by_email (uses .filter(...).order_by(...).first())."""

    def test_returns_active_session(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from models import ScoutVettingSession

            mock_session = _make_session(candidate_email='jane@example.com')
            with patch.object(ScoutVettingSession, 'query') as mock_query:
                mock_query.filter.return_value.order_by.return_value.first.return_value = mock_session
                result = ScoutVettingService.find_session_by_email('jane@example.com')
                assert result is mock_session

    def test_returns_none_when_no_session(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from models import ScoutVettingSession

            with patch.object(ScoutVettingSession, 'query') as mock_query:
                mock_query.filter.return_value.order_by.return_value.first.return_value = None
                result = ScoutVettingService.find_session_by_email('nobody@example.com')
                assert result is None


class TestActiveSessionDedup:
    """Test _check_active_session_exists (uses .filter(...).first())."""

    def test_returns_true_when_exists(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from models import ScoutVettingSession

            svc = ScoutVettingService(email_service=None)
            with patch.object(ScoutVettingSession, 'query') as mock_query:
                mock_query.filter.return_value.first.return_value = _make_session()
                result = svc._check_active_session_exists(5001, 3001)
                assert result is True

    def test_returns_false_when_not_exists(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from models import ScoutVettingSession

            svc = ScoutVettingService(email_service=None)
            with patch.object(ScoutVettingSession, 'query') as mock_query:
                mock_query.filter.return_value.first.return_value = None
                result = svc._check_active_session_exists(5001, 3001)
                assert result is False


class TestGetConversationSummary:
    """Test _get_conversation_summary."""

    def test_empty_conversation(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from models import VettingConversationTurn

            svc = ScoutVettingService(email_service=None)
            with patch.object(VettingConversationTurn, 'query') as mock_query:
                mock_query.filter_by.return_value.order_by.return_value.all.return_value = []
                result = svc._get_conversation_summary(_make_session())
                assert isinstance(result, str)

    def test_conversation_with_turns(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from models import VettingConversationTurn

            mock_turn = Mock()
            mock_turn.direction = 'outbound'
            mock_turn.body_text = 'Hello, we have a few questions.'
            mock_turn.created_at = datetime.utcnow()

            svc = ScoutVettingService(email_service=None)
            with patch.object(VettingConversationTurn, 'query') as mock_query:
                mock_query.filter_by.return_value.order_by.return_value.all.return_value = [mock_turn]
                result = svc._get_conversation_summary(_make_session())
                assert len(result) > 0


class TestRecordTurn:
    """Test _record_turn (calls db.session.add only, no commit)."""

    def test_records_outbound_turn(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from app import db

            svc = ScoutVettingService(email_service=None)
            session = _make_session()

            added_items = []
            with patch.object(db.session, 'add', side_effect=lambda item: added_items.append(item)):
                svc._record_turn(
                    session=session,
                    direction='outbound',
                    subject='Re: Follow-up [SV-42]',
                    body='Here are some questions.',
                    questions_asked=['Q1', 'Q2']
                )
                assert len(added_items) == 1

    def test_records_inbound_turn(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from app import db

            svc = ScoutVettingService(email_service=None)
            session = _make_session()

            added_items = []
            with patch.object(db.session, 'add', side_effect=lambda item: added_items.append(item)):
                svc._record_turn(
                    session=session,
                    direction='inbound',
                    subject='Re: Follow-up [SV-42]',
                    body='My experience is 5 years.',
                    ai_intent='answer',
                    ai_reasoning='Candidate directly answered',
                    answers_extracted={'Q1': '5 years'}
                )
                assert len(added_items) == 1


class TestInitiateVetting:
    """Test initiate_vetting."""

    def test_skips_when_disabled(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService

            svc = ScoutVettingService(email_service=Mock())
            with patch.object(svc, 'is_enabled', return_value=False), \
                 patch.object(svc, '_get_enabled_at', return_value=None), \
                 patch.object(svc, 'is_enabled_for_job', return_value=False):
                result = svc.initiate_vetting(_make_vetting_log(), [_make_match()])
                # When is_enabled_for_job returns False, matches are skipped
                assert result['created'] == 0


class TestClassifyReply:
    """Test _classify_reply with mocked Claude."""

    def test_classify_answer_reply(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from models import VettingConversationTurn

            svc = ScoutVettingService(email_service=None)
            mock_llm = Mock()
            mock_llm.invoke.return_value.content = json.dumps({
                'intent': 'answer',
                'reasoning': 'Candidate answered questions directly',
                'answers_extracted': {'What is your experience with Python?': '5 years experience'},
                'candidate_questions': []
            })

            with patch.object(type(svc), 'llm', new_callable=PropertyMock, return_value=mock_llm), \
                 patch.object(VettingConversationTurn, 'query') as mock_query:
                mock_query.filter_by.return_value.order_by.return_value.all.return_value = []
                result = svc._classify_reply(_make_session(), 'I have 5 years experience.')
                assert result['intent'] == 'answer'

    def test_classify_decline_reply(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from models import VettingConversationTurn

            svc = ScoutVettingService(email_service=None)
            mock_llm = Mock()
            mock_llm.invoke.return_value.content = json.dumps({
                'intent': 'decline',
                'reasoning': 'Candidate explicitly declined',
                'answers_extracted': {},
                'candidate_questions': []
            })

            with patch.object(type(svc), 'llm', new_callable=PropertyMock, return_value=mock_llm), \
                 patch.object(VettingConversationTurn, 'query') as mock_query:
                mock_query.filter_by.return_value.order_by.return_value.all.return_value = []
                result = svc._classify_reply(_make_session(), 'I am not interested.')
                assert result['intent'] == 'decline'

    def test_classify_handles_llm_failure(self, app):
        with app.app_context():
            from scout_vetting_service import ScoutVettingService
            from models import VettingConversationTurn

            svc = ScoutVettingService(email_service=None)
            mock_llm = Mock()
            mock_llm.invoke.side_effect = Exception('LLM timeout')

            with patch.object(type(svc), 'llm', new_callable=PropertyMock, return_value=mock_llm), \
                 patch.object(VettingConversationTurn, 'query') as mock_query:
                mock_query.filter_by.return_value.order_by.return_value.all.return_value = []
                result = svc._classify_reply(_make_session(), 'Some reply.')
                # Falls back to safe default
                assert result['intent'] in ('answer', 'unrelated', 'error')


# ===========================================================================
# Route integration tests
# ===========================================================================

class TestScoutVettingDashboard:
    """Test /scout-vetting dashboard route."""

    def test_dashboard_requires_auth(self, client, app):
        with app.app_context():
            response = client.get('/vetting')
            assert response.status_code in (302, 301)

    def test_dashboard_loads_when_authenticated(self, authenticated_client, app):
        with app.app_context():
            from models import ScoutVettingSession, VettingConfig
            with patch.object(ScoutVettingSession, 'query') as mock_query, \
                 patch.object(VettingConfig, 'get_value', return_value=None):
                mock_query.count.return_value = 0
                mock_query.filter.return_value.count.return_value = 0
                mock_query.filter_by.return_value.count.return_value = 0
                mock_query.order_by.return_value.limit.return_value.all.return_value = []

                response = authenticated_client.get('/vetting')
                assert response.status_code == 200
                assert b'Scout Vetting' in response.data


class TestScoutVettingCronEndpoint:
    """Test /api/cron/scout-vetting-followups endpoint."""

    def test_cron_requires_auth(self, client, app):
        with app.app_context():
            response = client.post('/api/cron/scout-vetting-followups')
            assert response.status_code in (401, 503)

    def test_cron_with_valid_token(self, client, app):
        with app.app_context():
            with patch.dict('os.environ', {'CRON_SECRET': 'test-secret-123'}):
                from models import VettingConfig, ScoutVettingSession
                with patch.object(VettingConfig, 'get_value', return_value='true'), \
                     patch.object(ScoutVettingSession, 'query') as mock_sq:
                    # run_followups queries return empty
                    mock_sq.filter.return_value.all.return_value = []

                    from app import db
                    with patch.object(db.session, 'query') as mock_db_query:
                        mock_db_query.return_value.filter.return_value.distinct.return_value.all.return_value = []

                        response = client.post(
                            '/api/cron/scout-vetting-followups',
                            headers={'Authorization': 'Bearer test-secret-123'}
                        )
                        assert response.status_code == 200
                        data = response.get_json()
                        assert data['success'] is True

    def test_cron_when_disabled(self, client, app):
        with app.app_context():
            with patch.dict('os.environ', {'CRON_SECRET': 'test-secret-123'}):
                from models import VettingConfig
                with patch.object(VettingConfig, 'get_value', return_value=None):
                    response = client.post(
                        '/api/cron/scout-vetting-followups',
                        headers={'Authorization': 'Bearer test-secret-123'}
                    )
                    assert response.status_code == 200
                    data = response.get_json()
                    assert data['success'] is True
                    assert 'disabled' in data['message'].lower()


class TestInboundEmailRouting:
    """Test inbound email webhook routing."""

    def test_scout_vetting_email_routed(self, client, app):
        with app.app_context():
            from models import ScoutVettingSession
            with patch.object(ScoutVettingSession, 'query') as mock_query:
                mock_query.get.return_value = None
                mock_query.filter.return_value.order_by.return_value.first.return_value = None

                response = client.post('/api/email/inbound', data={
                    'to': 'scout-vetting@parse.lyntrix.ai',
                    'from': 'jane@example.com',
                    'subject': 'Re: Quick follow-up [SV-42]',
                    'text': 'Here are my answers...',
                })
                assert response.status_code == 200
                data = response.get_json()
                assert data is not None

    def test_normal_email_not_routed_to_scout_vetting(self, client, app):
        with app.app_context():
            with patch('email_inbound_service.EmailInboundService') as MockInbound:
                mock_instance = Mock()
                mock_instance.process_email.return_value = {'success': True}
                MockInbound.return_value = mock_instance

                response = client.post('/api/email/inbound', data={
                    'to': 'jobs@parse.lyntrix.ai',
                    'from': 'someone@example.com',
                    'subject': 'Job application',
                    'text': 'Resume attached',
                })
                assert response.status_code == 200
