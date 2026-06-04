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


def _build_multipart(boundary, fields, files=None):
    """Construct a raw multipart/form-data body with a chosen boundary.

    `fields`: dict of name -> str value.
    `files`: list of (name, filename, content_bytes, content_type).
    """
    crlf = b'\r\n'
    b = boundary.encode('ascii')
    out = b''
    for name, value in fields.items():
        out += b'--' + b + crlf
        out += (
            f'Content-Disposition: form-data; name="{name}"'.encode() + crlf + crlf
        )
        out += value.encode('utf-8') + crlf
    for name, filename, content, ctype in (files or []):
        out += b'--' + b + crlf
        out += (
            f'Content-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"'.encode() + crlf
        )
        out += f'Content-Type: {ctype}'.encode() + crlf + crlf
        out += content + crlf
    out += b'--' + b + b'--' + crlf
    return out


class TestTolerantMultipart:
    """Unit coverage for the tolerant multipart parser + boundary sniffing —
    the core of the Jun 4 incident fix (Werkzeug parsed a present body into
    zero parts because the body's boundary did not match the declared one)."""

    def test_parses_normal_multipart(self):
        from routes.email import _parse_multipart_tolerant

        body = _build_multipart(
            'GoodBoundary123',
            {'from': 'Kyle <kyle@example.com>', 'subject': 'QA (35115)'},
            [('attachment1', 'resume.pdf', b'%PDF-1.4 data', 'application/pdf')],
        )
        result = _parse_multipart_tolerant(
            body, 'multipart/form-data; boundary=GoodBoundary123'
        )
        assert 'kyle@example.com' in result['from']
        assert '35115' in result['subject']
        atts = json.loads(result['attachments'])
        assert atts[0]['filename'] == 'resume.pdf'
        assert base64.b64decode(atts[0]['content']) == b'%PDF-1.4 data'

    def test_recovers_when_declared_boundary_mismatches_body(self):
        """The production failure: Content-Type declares one boundary, the body
        uses another → sniff the real boundary out of the body and recover."""
        from routes.email import _parse_multipart_tolerant

        body = _build_multipart(
            'ACTUAL_xYzZY',
            {'from': 'Kyle <kyle@example.com>', 'subject': 'QA (35115)'},
            [('attachment1', 'resume.pdf', b'%PDF-1.4 data', 'application/pdf')],
        )
        # Declared boundary is WRONG on purpose.
        result = _parse_multipart_tolerant(
            body, 'multipart/form-data; boundary=DECLARED_does_not_match'
        )
        assert 'kyle@example.com' in result['from']
        assert '35115' in result['subject']
        assert 'attachments' in result

    def test_sniff_boundary(self):
        from routes.email import _sniff_multipart_boundary

        body = _build_multipart('xYzZY', {'subject': 'hi'})
        assert _sniff_multipart_boundary(body) == 'xYzZY'

    def test_sniff_boundary_empty_body(self):
        from routes.email import _sniff_multipart_boundary
        assert _sniff_multipart_boundary(b'') is None

    def test_sanitize_snippet_strips_binary(self):
        from routes.email import _sanitize_body_snippet
        snippet = _sanitize_body_snippet(b'--bound\r\nName: x\x00\x01\xff binary')
        assert '--bound' in snippet
        assert '\x00' not in snippet


class TestWebhookBoundaryMismatch:
    """End-to-end regression for the live incident: a real multipart body whose
    boundary does not match the declared Content-Type boundary. Werkzeug's
    strict parser yields zero parts (the bug); the webhook must still recover
    the candidate via the tolerant path."""

    def test_boundary_mismatch_is_recovered(self, client, monkeypatch):
        captured = {}

        def _fake_process(app_ref, payload, is_scout_vetting=False):
            captured['payload'] = payload

        monkeypatch.setattr('routes.email._process_email_in_background', _fake_process)

        body = _build_multipart(
            'ACTUAL_xYzZY',
            {
                'from': 'Kyle Roots <kyle@example.com>',
                'to': 'apply@myticas.com',
                'subject': 'Senior QA Lead (35115) - Kyle Roots has applied',
                'text': 'resume attached',
            },
            [('attachment1', 'resume.pdf', b'%PDF-1.4 data', 'application/pdf')],
        )

        resp = client.post(
            '/api/email/inbound',
            data=body,
            content_type='multipart/form-data; boundary=DECLARED_does_not_match',
        )
        assert resp.status_code == 200

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
        atts = json.loads(payload['attachments'])
        assert atts[0]['filename'] == 'resume.pdf'

    def test_normal_multipart_with_matching_boundary_still_works(self, client, monkeypatch):
        """A well-formed multipart body (boundary matches) must parse via the
        Werkzeug primary path, untouched by the recovery layers."""
        captured = {}

        def _fake_process(app_ref, payload, is_scout_vetting=False):
            captured['payload'] = payload

        monkeypatch.setattr('routes.email._process_email_in_background', _fake_process)

        body = _build_multipart(
            'Matching123',
            {
                'from': 'recruiter@partner.com',
                'to': 'apply@myticas.com',
                'subject': 'Real candidate',
                'text': 'see resume',
            },
        )
        resp = client.post(
            '/api/email/inbound',
            data=body,
            content_type='multipart/form-data; boundary=Matching123',
        )
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
        assert 'attachments' not in payload


class _ShortReadStream:
    """A stream that returns at most `chunk` bytes per read() call — mimics the
    gunicorn/Werkzeug short read that truncated large inbound bodies to ~4 KB
    (the real Jun 4 root cause: candidate created without résumé, or 'None None'
    ignored, depending on how far the partial read reached)."""

    def __init__(self, data, chunk=4096):
        self._data = data
        self._pos = 0
        self._chunk = chunk

    def read(self, size=-1):
        if self._pos >= len(self._data):
            return b''
        if size is None or size < 0:
            size = len(self._data) - self._pos
        n = min(size, self._chunk, len(self._data) - self._pos)
        out = self._data[self._pos:self._pos + n]
        self._pos += n
        return out


class _FakeReq:
    def __init__(self, data, chunk=4096, content_length=None):
        self.content_length = len(data) if content_length is None else content_length
        self.stream = _ShortReadStream(data, chunk)


class TestFullBodyRead:
    """Core of the Jun 4 fix: a single read() short-returns ~4 KB while the body
    is still buffering, truncating large multipart uploads. _read_full_request_body
    must loop until the entire content_length is assembled."""

    def test_assembles_full_body_past_short_reads(self):
        from routes.email import _read_full_request_body
        big = b'A' * 143517  # ~the real production body size
        req = _FakeReq(big, chunk=4096)
        result = _read_full_request_body(req)
        assert len(result) == len(big)
        assert result == big

    def test_full_multipart_with_attachment_survives_short_reads(self):
        """The end-to-end shape: a multipart body whose résumé attachment sits
        past the first 4 KB must come through whole, so the attachment parses."""
        from routes.email import _read_full_request_body, _parse_multipart_tolerant
        body = _build_multipart(
            'xYzZY',
            {
                'from': 'Kyle Roots <kyle@example.com>',
                'subject': 'Senior QA Lead (35115) - Kyle Roots has applied',
            },
            [('attachment1', 'resume.pdf', b'%PDF-1.4 ' + b'R' * 50000,
              'application/pdf')],
        )
        req = _FakeReq(body, chunk=4096)
        assembled = _read_full_request_body(req)
        assert assembled == body
        result = _parse_multipart_tolerant(
            assembled, 'multipart/form-data; boundary=xYzZY'
        )
        assert '35115' in result['subject']
        atts = json.loads(result['attachments'])
        assert atts[0]['filename'] == 'resume.pdf'
        assert base64.b64decode(atts[0]['content']) == b'%PDF-1.4 ' + b'R' * 50000

    def test_handles_missing_content_length_reads_to_eof(self):
        from routes.email import _read_full_request_body
        data = b'hello world ' * 5000
        req = _FakeReq(data, chunk=512, content_length=0)
        assert _read_full_request_body(req) == data

    def test_stream_access_failure_is_fail_soft(self):
        from routes.email import _read_full_request_body

        class _BadReq:
            content_length = 100

            @property
            def stream(self):
                raise RuntimeError("no stream")

        assert _read_full_request_body(_BadReq()) == b''

    def test_read_error_is_fail_soft(self):
        from routes.email import _read_full_request_body

        class _ErrStream:
            def read(self, size=-1):
                raise IOError("boom")

        class _R:
            content_length = 100
            stream = _ErrStream()

        assert _read_full_request_body(_R()) == b''


class TestTruncatedBodyRequestsRetry:
    """Option A safety net (Jun 4): when the body arrives truncated/empty relative
    to its declared content_length — the production load balancer hands the worker
    fewer bytes than promised — the webhook must return 503 (not 200) so SendGrid
    RETRIES the delivery instead of the candidate being silently lost."""

    def test_empty_body_returns_503_for_retry(self, client, monkeypatch):
        called = {'bg': False}

        def _should_not_run(*a, **k):
            called['bg'] = True

        monkeypatch.setattr('routes.email._process_email_in_background', _should_not_run)
        # Simulate the load balancer delivering 0 bytes while content_length is set.
        monkeypatch.setattr('routes.email._read_full_request_body', lambda req: b'')

        body = _build_multipart(
            'xYzZY',
            {'from': 'Kyle Roots <kyle@example.com>', 'to': 'apply@myticas.com',
             'subject': 'Data Architect (35185) - Kyle Roots has applied'},
            [('attachment1', 'resume.pdf', b'%PDF-1.4 ' + b'R' * 50000,
              'application/pdf')],
        )
        resp = client.post(
            '/api/email/inbound',
            data=body,
            content_type='multipart/form-data; boundary=xYzZY',
        )
        assert resp.status_code == 503
        assert resp.get_json()['error'] == 'incomplete_request_body'
        assert called['bg'] is False, "must not process a truncated email"

    def test_partial_body_returns_503_for_retry(self, client, monkeypatch):
        monkeypatch.setattr('routes.email._process_email_in_background',
                            lambda *a, **k: None)
        # Deliver only the first ~4 KB of a large multipart body.
        monkeypatch.setattr('routes.email._read_full_request_body',
                            lambda req: b'X' * 4096)

        body = _build_multipart(
            'xYzZY',
            {'from': 'Kyle Roots <kyle@example.com>', 'to': 'apply@myticas.com',
             'subject': 'Data Architect (35185) - Kyle Roots has applied'},
            [('attachment1', 'resume.pdf', b'%PDF-1.4 ' + b'R' * 50000,
              'application/pdf')],
        )
        resp = client.post(
            '/api/email/inbound',
            data=body,
            content_type='multipart/form-data; boundary=xYzZY',
        )
        assert resp.status_code == 503
        payload = resp.get_json()
        assert payload['received_bytes'] == 4096
        assert payload['expected_bytes'] > 4096

    def test_complete_body_still_returns_200(self, client, monkeypatch):
        """The happy path must be untouched: a full body is processed and 200'd,
        never mistaken for a truncation."""
        captured = {}

        def _fake_process(app_ref, payload, is_scout_vetting=False):
            captured['payload'] = payload

        monkeypatch.setattr('routes.email._process_email_in_background', _fake_process)

        body = _build_multipart(
            'Matching123',
            {'from': 'recruiter@partner.com', 'to': 'apply@myticas.com',
             'subject': 'Real candidate', 'text': 'see resume'},
        )
        resp = client.post(
            '/api/email/inbound',
            data=body,
            content_type='multipart/form-data; boundary=Matching123',
        )
        assert resp.status_code == 200


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
