"""
Tests for the inbound webhook raw-MIME fail-soft fallback.

Background (Jun 2026 production incident): inbound candidate emails reached
the SendGrid Inbound Parse webhook but arrived with EMPTY pre-parsed fields
(blank from/subject, no attachment) — every real applicant since 22:32 UTC
Jun 3 was filed as empty/ignored and silently lost. The leading cause is
SendGrid posting the raw, full MIME message (raw mode / payload-shape change)
instead of the parsed form fields the pipeline expects.

The fix (routes/email.py): when the parsed fields are missing, reconstruct
from/subject/text/html/headers/attachments from the raw MIME message
(stdlib `email`). It must:
  - recover a real candidate from raw MIME (the `email` field),
  - NEVER touch a normally-parsed payload,
  - never raise (fail-soft).
"""
import base64
import json
from email.message import EmailMessage

import pytest


def _build_raw_mime(*, sender, subject, body_text,
                    attachment_name=None, attachment_bytes=None,
                    attachment_type='application/pdf'):
    """Construct a raw RFC822 MIME message string, optionally with attachment."""
    msg = EmailMessage()
    msg['From'] = sender
    msg['To'] = 'apply@myticas.com'
    msg['Subject'] = subject
    msg['Message-ID'] = '<raw-mime-test-123@example.com>'
    msg.set_content(body_text)
    if attachment_name and attachment_bytes is not None:
        maintype, _, subtype = attachment_type.partition('/')
        msg.add_attachment(
            attachment_bytes,
            maintype=maintype or 'application',
            subtype=subtype or 'octet-stream',
            filename=attachment_name,
        )
    return msg.as_string()


class TestParseRawMime:
    def test_recovers_core_fields_and_attachment(self):
        from routes.email import _parse_raw_mime

        pdf_bytes = b'%PDF-1.4 fake resume bytes'
        raw = _build_raw_mime(
            sender='Kyle Roots <kyle@example.com>',
            subject='Senior QA Lead (35115) - Kyle Roots has applied on LinkedIn',
            body_text='Please find my resume attached.',
            attachment_name='Kyle_Roots_Resume.pdf',
            attachment_bytes=pdf_bytes,
        )

        recovered = _parse_raw_mime(raw)

        assert 'kyle@example.com' in recovered['from']
        assert '35115' in recovered['subject']
        assert 'resume attached' in recovered['text']
        assert 'Message-ID' in recovered['headers']

        # Attachment must match the JSON-list shape _extract_attachments reads.
        atts = json.loads(recovered['attachments'])
        assert len(atts) == 1
        assert atts[0]['filename'] == 'Kyle_Roots_Resume.pdf'
        assert base64.b64decode(atts[0]['content']) == pdf_bytes
        assert atts[0]['type'] == 'application/pdf'

    def test_no_attachment_key_when_none(self):
        from routes.email import _parse_raw_mime

        raw = _build_raw_mime(
            sender='jane@example.com',
            subject='Application',
            body_text='hello',
        )
        recovered = _parse_raw_mime(raw)
        assert 'attachments' not in recovered

    def test_accepts_bytes_input(self):
        from routes.email import _parse_raw_mime

        raw = _build_raw_mime(
            sender='bob@example.com', subject='Hi', body_text='body',
        ).encode('utf-8')
        recovered = _parse_raw_mime(raw)
        assert 'bob@example.com' in recovered['from']


class TestHasParsedFields:
    def test_true_when_subject_present(self):
        from routes.email import _has_parsed_email_fields
        assert _has_parsed_email_fields({'subject': 'hi'}, None) is True

    def test_true_when_attachment_key_present(self):
        from routes.email import _has_parsed_email_fields
        assert _has_parsed_email_fields({'attachment1': b'x'}, None) is True

    def test_false_when_all_empty(self):
        from routes.email import _has_parsed_email_fields
        assert _has_parsed_email_fields(
            {'from': '', 'subject': '', 'text': '', 'html': ''}, None
        ) is False

    def test_false_when_attachments_is_zero_count(self):
        """SendGrid sends 'attachments' as a count field; '0' must NOT count as
        a usable signal (otherwise the fallback is wrongly suppressed)."""
        from routes.email import _has_parsed_email_fields
        assert _has_parsed_email_fields(
            {'from': '', 'subject': '', 'text': '', 'html': '', 'attachments': '0'},
            None,
        ) is False

    def test_true_when_attachments_is_positive_count(self):
        from routes.email import _has_parsed_email_fields
        assert _has_parsed_email_fields({'attachments': '2'}, None) is True


class TestWebhookFallback:
    """End-to-end through the Flask test client (webhook ingress)."""

    def test_raw_mime_email_field_is_recovered(self, client, monkeypatch):
        """SendGrid raw mode: only an `email` field with raw MIME → the webhook
        must reconstruct from/subject and queue real processing (not empty)."""
        captured = {}

        def _fake_process(app_ref, payload, is_scout_vetting=False):
            captured['payload'] = payload

        monkeypatch.setattr('routes.email._process_email_in_background', _fake_process)

        raw = _build_raw_mime(
            sender='Kyle Roots <kyle@example.com>',
            subject='Senior QA Lead (35115) - Kyle Roots has applied',
            body_text='resume attached',
            attachment_name='resume.pdf',
            attachment_bytes=b'%PDF-1.4 data',
        )

        resp = client.post('/api/email/inbound', data={'email': raw})
        assert resp.status_code == 200

        # Background thread is started; give it a beat to capture.
        import time
        for _ in range(50):
            if 'payload' in captured:
                break
            time.sleep(0.01)

        assert 'payload' in captured, "background processor never invoked"
        payload = captured['payload']
        assert 'kyle@example.com' in payload['from']
        assert '35115' in payload['subject']
        assert 'attachments' in payload

    def test_normal_parsed_payload_is_untouched(self, client, monkeypatch):
        """A normal parsed SendGrid payload must NOT be altered by the fallback."""
        captured = {}

        def _fake_process(app_ref, payload, is_scout_vetting=False):
            captured['payload'] = payload

        monkeypatch.setattr('routes.email._process_email_in_background', _fake_process)

        resp = client.post('/api/email/inbound', data={
            'from': 'recruiter@partner.com',
            'to': 'apply@myticas.com',
            'subject': 'Real candidate',
            'text': 'see resume',
        })
        assert resp.status_code == 200

        import time
        for _ in range(50):
            if 'payload' in captured:
                break
            time.sleep(0.01)

        assert 'payload' in captured
        payload = captured['payload']
        assert payload['from'] == 'recruiter@partner.com'
        assert payload['subject'] == 'Real candidate'
        # Fallback must not have injected an attachments key.
        assert 'attachments' not in payload

    def test_incident_shape_attachments_zero_plus_raw_email(self, client, monkeypatch):
        """Exact production incident shape: blank parsed fields + attachments='0'
        count + a raw `email` field. The fallback must engage and recover."""
        captured = {}

        def _fake_process(app_ref, payload, is_scout_vetting=False):
            captured['payload'] = payload

        monkeypatch.setattr('routes.email._process_email_in_background', _fake_process)

        raw = _build_raw_mime(
            sender='Kyle Roots <kyle@example.com>',
            subject='Senior QA Lead (35115) - Kyle Roots has applied',
            body_text='resume attached',
            attachment_name='resume.pdf',
            attachment_bytes=b'%PDF-1.4 data',
        )

        resp = client.post('/api/email/inbound', data={
            'from': '', 'subject': '', 'text': '', 'html': '',
            'attachments': '0',
            'email': raw,
        })
        assert resp.status_code == 200

        import time
        for _ in range(50):
            if 'payload' in captured:
                break
            time.sleep(0.01)

        assert 'payload' in captured
        payload = captured['payload']
        assert 'kyle@example.com' in payload['from']
        assert '35115' in payload['subject']
        assert 'attachments' in payload
        atts = json.loads(payload['attachments'])
        assert atts[0]['filename'] == 'resume.pdf'

    def test_non_form_raw_body_is_recovered(self, client, monkeypatch):
        """Non-form POST (content-type text/plain, raw MIME in the body) must be
        read via request.get_data() and recovered."""
        captured = {}

        def _fake_process(app_ref, payload, is_scout_vetting=False):
            captured['payload'] = payload

        monkeypatch.setattr('routes.email._process_email_in_background', _fake_process)

        raw = _build_raw_mime(
            sender='jane@example.com',
            subject='Application for 35115',
            body_text='hi there',
        )

        resp = client.post('/api/email/inbound', data=raw, content_type='text/plain')
        assert resp.status_code == 200

        import time
        for _ in range(50):
            if 'payload' in captured:
                break
            time.sleep(0.01)

        assert 'payload' in captured
        payload = captured['payload']
        assert 'jane@example.com' in payload['from']
        assert '35115' in payload['subject']

    def test_truly_empty_post_does_not_crash(self, client, monkeypatch):
        """A genuinely empty POST (noise) must return 200 and not crash."""
        monkeypatch.setattr(
            'routes.email._process_email_in_background',
            lambda *a, **k: None,
        )
        resp = client.post('/api/email/inbound', data={})
        assert resp.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
