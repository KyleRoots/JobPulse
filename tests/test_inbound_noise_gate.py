"""
Regression tests for the inbound-email non-candidate noise gate.

Background (Jun 2026 production incident): the SendGrid inbound webhook
received a flood of malformed/automated emails (blank sender, blank
subject, no attachment, nothing extractable). Each one was treated as a
failed candidate submission and fired a "[Scout Genius] Candidate Parse
Failure — None None" admin alert, flooding recruiter inboxes.

The fix adds a noise gate in process_email(): an inbound message with no
attachment, no extractable name/contact, AND a blank sender or subject is
recorded for audit (status='ignored') but does NOT trigger an admin alert.
Genuine parse failures (a real candidate whose attachment can't be read)
must STILL alert.
"""
import pytest
from unittest.mock import MagicMock


def _make_service(monkeypatch, *, attachments, resume_file,
                  source='Other', job_id=None, email_candidate=None):
    """Build an EmailInboundService with all extraction/AI collaborators
    stubbed so process_email runs hermetically (no network, no DB-less AI)."""
    from email_inbound_service import EmailInboundService

    svc = EmailInboundService()
    svc.detect_source = MagicMock(return_value=source)
    svc.extract_bullhorn_job_id = MagicMock(return_value=job_id)
    svc.extract_candidate_from_email = MagicMock(return_value=email_candidate or {})
    svc._extract_attachments = MagicMock(return_value=attachments)
    svc._select_best_resume = MagicMock(return_value=resume_file)
    # All deterministic + AI recovery layers find nothing.
    svc._last_resort_ai_extraction = MagicMock(return_value=None)
    # Track admin alerts without sending email.
    svc._notify_admin_parse_failure = MagicMock()
    return svc


class TestInboundNoiseGate:
    def test_blank_junk_email_is_ignored_no_alert(self, app):
        """Blank sender + blank subject + no attachment + no info →
        recorded as 'ignored', NO admin parse-failure alert."""
        from app import db
        from models import ParsedEmail

        with app.app_context():
            svc = _make_service(monkeypatch=None, attachments=[], resume_file=None)
            payload = {'from': '', 'to': 'apply@scoutgenius.ai',
                       'subject': '', 'text': '', 'html': ''}

            result = svc.process_email(payload)

            assert result['ignored'] is True
            assert result['success'] is False
            svc._notify_admin_parse_failure.assert_not_called()

            row = ParsedEmail.query.get(result['parsed_email_id'])
            assert row is not None
            assert row.status == 'ignored'

    def test_real_sender_and_subject_still_alerts(self, app):
        """A message with a real sender AND subject but no attachment/info
        is NOT noise — the admin alert must still fire (conservative gate)."""
        from app import db
        from models import ParsedEmail

        with app.app_context():
            svc = _make_service(monkeypatch=None, attachments=[], resume_file=None)
            payload = {'from': 'recruiter@partner.com', 'to': 'apply@scoutgenius.ai',
                       'subject': 'Candidate application', 'text': 'please review', 'html': ''}

            result = svc.process_email(payload)

            assert result['ignored'] is False
            svc._notify_admin_parse_failure.assert_called_once()

            row = ParsedEmail.query.get(result['parsed_email_id'])
            assert row.status == 'failed'

    def test_blank_subject_present_sender_no_attachment_is_ignored(self, app):
        """Policy boundary (intentional): blank subject alone — with a present
        sender, no attachment, no extractable info — is treated as noise."""
        from models import ParsedEmail

        with app.app_context():
            svc = _make_service(monkeypatch=None, attachments=[], resume_file=None)
            payload = {'from': 'someone@list.example.com', 'to': 'apply@scoutgenius.ai',
                       'subject': '', 'text': '', 'html': ''}

            result = svc.process_email(payload)

            assert result['ignored'] is True
            svc._notify_admin_parse_failure.assert_not_called()
            assert ParsedEmail.query.get(result['parsed_email_id']).status == 'ignored'

    def test_blank_sender_present_subject_no_attachment_is_ignored(self, app):
        """Policy boundary (intentional): blank sender alone — with a present
        subject, no attachment, no extractable info — is treated as noise."""
        from models import ParsedEmail

        with app.app_context():
            svc = _make_service(monkeypatch=None, attachments=[], resume_file=None)
            payload = {'from': '', 'to': 'apply@scoutgenius.ai',
                       'subject': 'Delivery Status Notification', 'text': '', 'html': ''}

            result = svc.process_email(payload)

            assert result['ignored'] is True
            svc._notify_admin_parse_failure.assert_not_called()
            assert ParsedEmail.query.get(result['parsed_email_id']).status == 'ignored'

    def test_real_candidate_unreadable_attachment_still_alerts(self, app):
        """A real candidate email WITH an (unsupported/unreadable) attachment
        must still alert so a recruiter can follow up manually."""
        from app import db
        from models import ParsedEmail

        with app.app_context():
            # Attachment present but _select_best_resume yields no usable resume.
            attachments = [{'filename': 'headshot.png', 'type': 'image/png', 'content': b'x'}]
            svc = _make_service(monkeypatch=None, attachments=attachments, resume_file=None)
            payload = {'from': 'jane@example.com', 'to': 'apply@scoutgenius.ai',
                       'subject': 'My application', 'text': 'see attached', 'html': ''}

            result = svc.process_email(payload)

            assert result['ignored'] is False
            svc._notify_admin_parse_failure.assert_called_once()

            row = ParsedEmail.query.get(result['parsed_email_id'])
            assert row.status == 'failed'


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
