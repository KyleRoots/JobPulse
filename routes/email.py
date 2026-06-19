import base64
import io
import json
import logging
import re
import threading
from datetime import datetime, timedelta, timezone
from flask import Blueprint, render_template, request, jsonify, current_app
from flask_login import login_required, current_user
from routes import register_admin_guard
from extensions import db, csrf

logger = logging.getLogger(__name__)
email_bp = Blueprint('email', __name__)
register_admin_guard(email_bp)


@email_bp.route('/vetting')
@login_required
def scout_vetting_dashboard():
    """Scout Vetting Module dashboard — shows session stats and recent activity."""
    from models import ScoutVettingSession
    from scout_vetting_service import ScoutVettingService

    svc = ScoutVettingService(email_service=None)
    is_enabled = svc.is_enabled()

    prod_filter = ScoutVettingSession.is_sandbox != True
    stats = {
        'total': ScoutVettingSession.query.filter(prod_filter).count(),
        'active': ScoutVettingSession.query.filter(
            ScoutVettingSession.status.in_(['outreach_sent', 'in_progress']),
            prod_filter
        ).count(),
        'awaiting_reply': ScoutVettingSession.query.filter_by(status='outreach_sent').filter(prod_filter).count(),
        'pending': ScoutVettingSession.query.filter_by(status='pending').filter(prod_filter).count(),
        'in_progress': ScoutVettingSession.query.filter_by(status='in_progress').filter(prod_filter).count(),
        'qualified': ScoutVettingSession.query.filter_by(status='qualified').filter(prod_filter).count(),
        'not_qualified': ScoutVettingSession.query.filter_by(status='not_qualified').filter(prod_filter).count(),
        'declined': ScoutVettingSession.query.filter_by(status='declined').filter(prod_filter).count(),
        'unresponsive': ScoutVettingSession.query.filter_by(status='unresponsive').filter(prod_filter).count(),
        'completed': 0,
    }
    stats['completed'] = stats['qualified'] + stats['not_qualified']

    sessions = ScoutVettingSession.query.filter(prod_filter).order_by(
        ScoutVettingSession.updated_at.desc()
    ).limit(50).all()

    return render_template('scout_vetting_dashboard.html',
                           stats=stats,
                           sessions=sessions,
                           is_enabled=is_enabled,
                           active_page='vetting')


def _process_email_in_background(app_ref, payload, is_scout_vetting=False):
    """Process an inbound email in a background thread to keep workers free."""
    with app_ref.app_context():
        try:
            if is_scout_vetting:
                logger.info("📧 [BG] Processing Scout Vetting inbound email")
                _handle_scout_vetting_inbound_bg(app_ref, payload)
            else:
                from email_inbound_service import EmailInboundService
                service = EmailInboundService()
                result = service.process_email(payload)
                if result['success']:
                    logger.info(f"✅ [BG] Email processed successfully: candidate {result.get('candidate_id')}")
                else:
                    logger.warning(f"⚠️ [BG] Email processing failed: {result.get('message')}")
        except Exception as e:
            logger.error(f"❌ [BG] Email processing error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())


def _decode_mime_header(value):
    """Decode an RFC2047-encoded MIME header into a clean unicode string."""
    if not value:
        return ''
    try:
        from email.header import decode_header, make_header
        return str(make_header(decode_header(str(value))))
    except Exception:
        return str(value)


def _has_parsed_email_fields(payload, files=None):
    """True if the payload already carries usable pre-parsed email fields.

    Used to decide whether the raw-MIME fallback needs to run. We only fall
    back when SendGrid gave us nothing usable (no sender, no subject, no body,
    no attachment) so a normal parsed payload is never touched.
    """
    if (payload.get('from') or '').strip():
        return True
    if (payload.get('subject') or '').strip():
        return True
    if (payload.get('text') or '').strip() or (payload.get('html') or '').strip():
        return True
    if files and len(files) > 0:
        return True
    # 'attachments' is a COUNT/metadata field in SendGrid payloads (e.g. '0'),
    # so its mere presence is NOT a usable signal — only a parseable non-empty
    # list or a positive count counts. Otherwise an empty email with
    # attachments='0' would wrongly suppress the raw-MIME fallback.
    atts = payload.get('attachments')
    if atts:
        try:
            parsed = json.loads(atts)
            if isinstance(parsed, list) and len(parsed) > 0:
                return True
            if isinstance(parsed, (int, float)) and parsed > 0:
                return True
        except Exception:
            pass
    if any(f'attachment{i}' in payload for i in range(1, 11)):
        return True
    return False


def _has_usable_attachments(value):
    """True only when `value` is a parseable, non-empty JSON attachment list.
    A SendGrid count field (e.g. '0') is NOT usable and must not block a
    recovered attachment list during the raw-MIME merge."""
    if not value:
        return False
    try:
        parsed = json.loads(value)
        return isinstance(parsed, list) and len(parsed) > 0
    except Exception:
        return False


def _parse_raw_mime(raw):
    """Reconstruct the SendGrid-style payload dict from a raw RFC822/MIME message.

    Fail-soft fallback for when SendGrid posts the raw, full MIME message
    (Inbound Parse "POST raw MIME" mode) instead of pre-parsed form fields —
    which otherwise leaves from/subject/text/attachments blank and silently
    drops real candidates. Only the keys we can recover are returned; the
    attachments key matches the JSON-list shape _extract_attachments expects.
    """
    from email.parser import BytesParser, Parser
    from email import policy

    if isinstance(raw, bytes):
        msg = BytesParser(policy=policy.default).parsebytes(raw)
    else:
        msg = Parser(policy=policy.default).parsestr(str(raw))

    recovered = {}
    recovered['from'] = _decode_mime_header(msg.get('From', ''))
    recovered['to'] = _decode_mime_header(msg.get('To', ''))
    recovered['subject'] = _decode_mime_header(msg.get('Subject', ''))
    # Preserve a headers blob so downstream Message-ID dedupe keeps working.
    try:
        recovered['headers'] = '\n'.join(f"{k}: {v}" for k, v in msg.items())
    except Exception:
        recovered['headers'] = ''

    text_parts = []
    html_parts = []
    attachments = []

    for part in msg.walk():
        if part.is_multipart():
            continue
        content_type = part.get_content_type()
        disposition = (part.get_content_disposition() or '')
        filename = part.get_filename()
        if disposition == 'attachment' or filename:
            try:
                content_bytes = part.get_payload(decode=True) or b''
            except Exception:
                content_bytes = b''
            attachments.append({
                'filename': _decode_mime_header(filename) or 'attachment',
                'content': base64.b64encode(content_bytes).decode('ascii'),
                'type': content_type,
            })
        elif content_type == 'text/plain':
            try:
                text_parts.append(part.get_content())
            except Exception:
                pass
        elif content_type == 'text/html':
            try:
                html_parts.append(part.get_content())
            except Exception:
                pass

    if text_parts:
        recovered['text'] = '\n'.join(text_parts)
    if html_parts:
        recovered['html'] = '\n'.join(html_parts)
    if attachments:
        recovered['attachments'] = json.dumps(attachments)

    return recovered


def _sniff_multipart_boundary(raw_bytes):
    """Find the actual boundary delimiter from the body itself.

    The leading cause of "valid body but zero parsed parts" is the multipart
    boundary inside the body not matching the one declared in the Content-Type
    header. Scanning the first lines for a ``--boundary`` delimiter lets us
    recover even when the declared boundary is wrong. Returns the token without
    the leading ``--`` (and without a trailing ``--``).
    """
    if not raw_bytes:
        return None
    head = raw_bytes[:8192]
    for line in head.split(b'\n')[:25]:
        s = line.strip()
        if s.startswith(b'--') and len(s) > 2:
            token = s[2:]
            if token.endswith(b'--'):
                token = token[:-2]
            if not token:
                continue
            try:
                return token.decode('ascii')
            except Exception:
                return None
    return None


def _parse_multipart_tolerant(raw_bytes, content_type):
    """Tolerant multipart/form-data parser for when Werkzeug's strict parser
    extracts nothing from an otherwise-present body.

    Uses the lenient stdlib email parser; if the declared boundary yields no
    usable parts it sniffs the real boundary out of the body and retries.
    Emits the same SendGrid-shaped payload (named fields + an 'attachments'
    JSON list matching what _extract_attachments consumes).
    """
    from email.parser import BytesParser
    from email import policy

    if not raw_bytes:
        return {}

    def _build_and_parse(ct):
        header = f"Content-Type: {ct}\r\nMIME-Version: 1.0\r\n\r\n".encode()
        msg = BytesParser(policy=policy.default).parsebytes(header + raw_bytes)
        if not msg.is_multipart():
            return {}
        fields = {}
        attachments = []
        for part in msg.iter_parts():
            filename = part.get_filename()
            name = part.get_param('name', header='content-disposition')
            if filename:
                try:
                    content_bytes = part.get_payload(decode=True) or b''
                except Exception:
                    content_bytes = b''
                attachments.append({
                    'filename': _decode_mime_header(filename) or 'attachment',
                    'content': base64.b64encode(content_bytes).decode('ascii'),
                    'type': part.get_content_type(),
                })
            elif name:
                try:
                    val = part.get_payload(decode=True)
                    fields[name] = val.decode('utf-8', 'replace') if val else ''
                except Exception:
                    fields[name] = ''
        result = dict(fields)
        if attachments:
            result['attachments'] = json.dumps(attachments)
        return result

    try:
        result = _build_and_parse(content_type)
    except Exception:
        result = {}
    if _has_parsed_email_fields(result, None):
        return result

    # Boundary mismatch fallback: re-parse using the boundary found in the body.
    sniffed = _sniff_multipart_boundary(raw_bytes)
    if sniffed:
        try:
            retry = _build_and_parse(f'multipart/form-data; boundary="{sniffed}"')
            if _has_parsed_email_fields(retry, None):
                return retry
        except Exception:
            pass
    return result


def _merge_recovered(payload, recovered):
    """Merge recovered fields into the payload, filling only empty keys so a
    normally-parsed payload is never overwritten. The 'attachments' key is
    special-cased: a SendGrid count (e.g. '0') is replaced by a recovered
    JSON list, but an already-usable list is kept."""
    if not recovered:
        return
    for key, value in recovered.items():
        if not value:
            continue
        if key == 'attachments':
            if not _has_usable_attachments(payload.get(key)):
                payload[key] = value
        elif not str(payload.get(key) or '').strip():
            payload[key] = value


def _sanitize_body_snippet(raw_bytes, limit=700):
    """Return a short, printable-only prefix of the raw body for diagnostics
    when recovery fails entirely — enough to reveal the boundary and part
    headers without dumping binary attachment data."""
    if not raw_bytes:
        return ''
    head = raw_bytes[:limit]
    try:
        text = head.decode('utf-8', 'replace')
    except Exception:
        text = repr(head)
    return ''.join(
        ch if (ch.isprintable() or ch in '\r\n\t') else '.' for ch in text
    )


def _read_full_request_body(req):
    """Read the ENTIRE request body, looping past short reads.

    Werkzeug/gunicorn can return a partial first chunk (~4 KB) from a single
    read() when the body has not fully buffered yet, silently truncating large
    multipart uploads. That truncation is the real cause of the inbound failure
    mode: a body cut off before the résumé attachment yields a candidate with no
    file, and one cut off before the sender/subject yields a 'None None' ignore.
    Loop on the input stream until we have read content_length bytes (or EOF)."""
    expected = req.content_length or 0
    try:
        stream = req.stream
    except Exception as e:
        logger.error(f"📧 Could not access request stream: {e}")
        return b''
    chunks = []
    total = 0
    while True:
        to_read = 65536
        if expected:
            remaining = expected - total
            if remaining <= 0:
                break
            to_read = min(to_read, remaining)
        try:
            chunk = stream.read(to_read)
        except Exception as e:
            logger.error(f"📧 Error reading request body chunk: {e}")
            break
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b''.join(chunks)


@email_bp.route('/api/email/inbound', methods=['GET', 'POST'])
@csrf.exempt
def email_inbound_webhook():
    """
    SendGrid Inbound Parse webhook endpoint
    
    Receives forwarded emails from job boards (LinkedIn, Dice, etc.)
    and processes them to create/update candidates in Bullhorn.
    
    This endpoint is public (no auth) because SendGrid needs to POST to it.
    Security is via SendGrid's signature verification.
    
    GET: Returns 200 OK for health checks / endpoint verification
    POST: Processes inbound email data from SendGrid
    """
    if request.method == 'GET':
        return jsonify({
            'status': 'ok',
            'endpoint': 'SendGrid Inbound Parse webhook',
            'methods': ['POST'],
            'message': 'Ready to receive emails'
        }), 200
    
    try:
        logger.info("📧 Received inbound email webhook")

        content_type = request.content_type or ''
        content_length = request.content_length
        ct_lower = content_type.lower()

        # Read the FULL body in a loop, up front, before form access consumes
        # the stream. A single read() short-returns ~4 KB while the body is
        # still buffering, which truncates large multipart uploads and silently
        # drops résumé attachments / produces 'None None' ignores. Looping until
        # content_length guarantees the whole message reaches the parser, and
        # holding the raw bytes also feeds the fail-soft recovery layers below.
        raw_body = _read_full_request_body(request)

        # Primary parse: Werkzeug, run against the cached raw bytes (so request
        # stream state is irrelevant). Handles the well-formed multipart and
        # urlencoded cases exactly as before.
        payload = {}
        form_keys, file_keys = [], []
        if raw_body and ('multipart/form-data' in ct_lower or
                         'x-www-form-urlencoded' in ct_lower):
            try:
                from werkzeug.formparser import parse_form_data
                environ = {
                    'REQUEST_METHOD': 'POST',
                    'CONTENT_TYPE': content_type,
                    'CONTENT_LENGTH': str(len(raw_body)),
                    'wsgi.input': io.BytesIO(raw_body),
                }
                _, form, files = parse_form_data(environ)
                payload = form.to_dict()
                for key, file in files.items():
                    payload[key] = file.read()
                    payload[f'{key}_info'] = {
                        'filename': file.filename,
                        'content_type': file.content_type,
                    }
                form_keys = list(form.keys())
                file_keys = list(files.keys())
            except Exception as e:
                logger.error(f"📧 Primary form parse failed: {e}")

        logger.info(
            "📧 Inbound payload diagnostic: content_type=%s content_length=%s "
            "body_len=%s form_keys=%s file_keys=%s",
            content_type, content_length, len(raw_body), form_keys, file_keys,
        )

        # If we still came up short after the looping read, the body was cut off
        # before it reached us (load balancer / upstream), NOT by a short read on
        # our side — flag it loudly so this distinct failure mode is not mistaken
        # for the old one.
        if content_length and len(raw_body) < content_length:
            logger.warning(
                "📧 Inbound body still short after full read: got %s of %s bytes "
                "— possible upstream truncation",
                len(raw_body), content_length,
            )
            # The résumé and/or sender fields live in the bytes we never received,
            # so this email cannot be processed. Returning 200 here would tell
            # SendGrid the message was handled and the candidate would be lost for
            # good. Return 503 instead so SendGrid RETRIES delivery — the
            # truncation is intermittent, so a resend usually arrives intact.
            logger.error(
                "📧 Inbound delivery incomplete — requesting SendGrid retry "
                "(got %s of %s bytes, content_type=%r)",
                len(raw_body), content_length, content_type,
            )
            return jsonify({
                'success': False,
                'error': 'incomplete_request_body',
                'message': ('Request body was truncated in transit; '
                            'please retry delivery.'),
                'expected_bytes': content_length,
                'received_bytes': len(raw_body),
            }), 503

        # ── Fail-soft recovery ──────────────────────────────────────────────
        # When the primary parse yields nothing usable (no sender/subject/body/
        # attachment) but a body IS present, a real candidate would be silently
        # dropped as empty/ignored. Recover from the raw bytes we cached above.
        if not _has_parsed_email_fields(payload, None):
            # Layer 1 — broken multipart/form-data: Werkzeug found no parts
            # (most often a body/header boundary mismatch). Re-parse tolerantly,
            # sniffing the real boundary out of the body if needed.
            if raw_body and 'multipart/form-data' in ct_lower:
                try:
                    _merge_recovered(
                        payload, _parse_multipart_tolerant(raw_body, content_type)
                    )
                except Exception as e:
                    logger.error(f"📧 Tolerant multipart parse failed: {e}")

            # Layer 2 — raw, full MIME message (Inbound Parse "POST raw MIME"
            # mode posts it as the 'email' field; a non-form body posts it as
            # the request body itself).
            if not _has_parsed_email_fields(payload, None):
                raw_mime = payload.get('email')
                if (not raw_mime and raw_body and
                        'multipart/form-data' not in ct_lower and
                        'x-www-form-urlencoded' not in ct_lower):
                    try:
                        raw_mime = raw_body.decode('utf-8', 'replace')
                    except Exception:
                        raw_mime = None
                if raw_mime:
                    try:
                        _merge_recovered(payload, _parse_raw_mime(raw_mime))
                    except Exception as e:
                        logger.error(f"📧 Raw-MIME fallback failed: {e}")

            if _has_parsed_email_fields(payload, None):
                logger.warning(
                    "📧 Inbound fallback engaged: recovered from=%r subject=%r "
                    "has_attachments=%s",
                    (payload.get('from') or '')[:80],
                    (payload.get('subject') or '')[:80],
                    _has_usable_attachments(payload.get('attachments')),
                )
            else:
                # Total failure: log a sanitized snippet + the sniffed boundary
                # so the exact malformation can be pinpointed without guessing.
                logger.error(
                    "📧 Inbound recovery FAILED — no usable fields. "
                    "content_type=%r body_len=%s sniffed_boundary=%r snippet=%r",
                    content_type, len(raw_body),
                    _sniff_multipart_boundary(raw_body),
                    _sanitize_body_snippet(raw_body),
                )

        to_field = payload.get('to', '')
        is_scout_vetting = 'scout-vetting@parse.lyntrix.ai' in to_field.lower()
        is_scout_support = 'support@scoutgenius.ai' in to_field.lower()
        
        if is_scout_vetting:
            logger.info("📧 Routing to Scout Vetting inbound handler (background)")
        elif is_scout_support:
            logger.info("📧 Routing to Scout Support inbound handler (background)")
        
        subject = payload.get('subject', 'unknown')
        logger.info(f"📧 Queuing email for background processing: {subject[:80]}")
        
        app_ref = current_app._get_current_object()

        if is_scout_support:
            thread = threading.Thread(
                target=_handle_scout_support_inbound_bg,
                args=(app_ref, payload),
                daemon=True
            )
        else:
            thread = threading.Thread(
                target=_process_email_in_background,
                args=(app_ref, payload, is_scout_vetting),
                daemon=True
            )
        thread.start()
        
        return jsonify({'success': True, 'message': 'Email accepted for processing'}), 200
            
    except Exception as e:
        logger.error(f"❌ Email inbound webhook error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 200


def _handle_scout_vetting_inbound(payload):
    """Process an inbound email routed to the Scout Vetting service.
    
    Called from email_inbound_webhook when the 'to' address matches
    scout-vetting@parse.lyntrix.ai.
    """
    try:
        from scout_vetting_service import ScoutVettingService
        from email_service import EmailService

        sender_email = payload.get('from', '')
        subject = payload.get('subject', '')
        text_body = payload.get('text', '')
        html_body = payload.get('html', '')

        email_match = re.search(r'[\w.+-]+@[\w.-]+', sender_email)
        sender_clean = email_match.group(0) if email_match else sender_email

        body = text_body
        if not body and html_body:
            body = re.sub(r'<[^>]+>', '', html_body)

        message_id = payload.get('Message-ID') or payload.get('message-id', '')

        logger.info(f"🔍 Scout Vetting inbound from {sender_clean}, subject: {subject}")

        session = ScoutVettingService.find_session_by_subject_token(subject)
        if not session:
            session = ScoutVettingService.find_session_by_email(sender_clean)

        if not session:
            logger.warning(f"⚠️ Scout Vetting: No active session found for {sender_clean}")
            return jsonify({
                'success': False,
                'message': 'No active vetting session found for this sender'
            }), 200

        svc = ScoutVettingService(email_service=EmailService())
        svc.process_candidate_reply(
            session=session,
            email_body=body,
            email_subject=subject,
            message_id=message_id
        )

        return jsonify({
            'success': True,
            'message': f'Scout Vetting reply processed for session {session.id}'
        }), 200

    except Exception as e:
        logger.error(f"❌ Scout Vetting inbound error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'message': str(e)}), 200


def _handle_scout_vetting_inbound_bg(app_ref, payload):
    """Background-thread version of Scout Vetting inbound processing."""
    try:
        from scout_vetting_service import ScoutVettingService
        from email_service import EmailService

        sender_email = payload.get('from', '')
        subject = payload.get('subject', '')
        text_body = payload.get('text', '')
        html_body = payload.get('html', '')

        email_match = re.search(r'[\w.+-]+@[\w.-]+', sender_email)
        sender_clean = email_match.group(0) if email_match else sender_email

        body = text_body
        if not body and html_body:
            body = re.sub(r'<[^>]+>', '', html_body)

        message_id = payload.get('Message-ID') or payload.get('message-id', '')

        logger.info(f"🔍 [BG] Scout Vetting inbound from {sender_clean}, subject: {subject}")

        session = ScoutVettingService.find_session_by_subject_token(subject)
        if not session:
            session = ScoutVettingService.find_session_by_email(sender_clean)

        if not session:
            logger.warning(f"⚠️ [BG] Scout Vetting: No active session found for {sender_clean}")
            return

        svc = ScoutVettingService(email_service=EmailService())
        svc.process_candidate_reply(
            session=session,
            email_body=body,
            email_subject=subject,
            message_id=message_id
        )
        logger.info(f"✅ [BG] Scout Vetting reply processed for session {session.id}")

    except Exception as e:
        logger.error(f"❌ [BG] Scout Vetting inbound error: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())


def _handle_scout_support_inbound_bg(app_ref, payload):
    with app_ref.app_context():
        try:
            from scout_support_service import ScoutSupportService

            sender_email = payload.get('from', '')
            subject = payload.get('subject', '')
            text_body = payload.get('text', '')
            html_body = payload.get('html', '')

            if not text_body and not html_body:
                raw_email = payload.get('email', '')
                if raw_email:
                    import email as email_lib
                    if isinstance(raw_email, bytes):
                        msg = email_lib.message_from_bytes(raw_email)
                    else:
                        msg = email_lib.message_from_string(raw_email)
                    if not sender_email:
                        sender_email = msg.get('From', '')
                    if not subject:
                        subject = msg.get('Subject', '')
                    if msg.is_multipart():
                        for part in msg.walk():
                            ctype = part.get_content_type()
                            if ctype == 'text/plain' and not text_body:
                                text_body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                            elif ctype == 'text/html' and not html_body:
                                html_body = part.get_payload(decode=True).decode('utf-8', errors='replace')
                    else:
                        payload_text = msg.get_payload(decode=True)
                        if payload_text:
                            text_body = payload_text.decode('utf-8', errors='replace')
                    logger.info(f"📨 [BG] Parsed raw MIME for Scout Support: text={len(text_body or '')} chars, html={len(html_body or '')} chars")

            email_match = re.search(r'[\w.+-]+@[\w.-]+', sender_email)
            sender_clean = email_match.group(0) if email_match else sender_email

            body = text_body
            if not body and html_body:
                body = re.sub(r'<[^>]+>', '', html_body)

            message_id = payload.get('Message-ID') or payload.get('message-id', '')
            if not message_id:
                raw_headers = payload.get('headers', '')
                if raw_headers:
                    mid_match = re.search(r'Message-ID:\s*(<[^>]+>)', raw_headers, re.IGNORECASE)
                    if mid_match:
                        message_id = mid_match.group(1)

            logger.info(f"🔍 [BG] Scout Support inbound from {sender_clean}, subject: {subject}")

            svc = ScoutSupportService()
            ticket = svc.find_ticket_by_email_subject(subject)

            if not ticket:
                logger.warning(f"⚠️ [BG] Scout Support: No ticket found for subject: {subject}")
                return

            is_admin = sender_clean.lower() == ticket.admin_email.lower()
            is_submitter = sender_clean.lower() == ticket.submitter_email.lower()

            reply_attachments = []
            for key in list(payload.keys()):
                info_key = f'{key}_info'
                if info_key in payload and isinstance(payload.get(key), bytes):
                    file_info = payload[info_key]
                    reply_attachments.append({
                        'filename': file_info.get('filename', 'attachment'),
                        'data': payload[key],
                        'content_type': file_info.get('content_type', 'application/octet-stream'),
                    })
            if reply_attachments:
                logger.info(f"📎 [BG] Scout Support reply has {len(reply_attachments)} attachment(s): {[a['filename'] for a in reply_attachments]}")

            if is_admin and ticket.status in ('awaiting_admin_approval', 'admin_clarifying'):
                svc.handle_admin_reply(ticket.id, body, message_id)
                logger.info(f"✅ [BG] Scout Support admin reply processed for ticket {ticket.ticket_number}")
            elif is_submitter:
                svc.handle_user_reply(ticket.id, body, message_id, attachment_data=reply_attachments or None)
                logger.info(f"✅ [BG] Scout Support user reply processed for ticket {ticket.ticket_number}")
            elif is_admin:
                svc.handle_admin_reply(ticket.id, body, message_id)
                logger.info(f"✅ [BG] Scout Support admin reply processed for ticket {ticket.ticket_number}")
            else:
                logger.warning(f"⚠️ [BG] Scout Support: Sender {sender_clean} not authorized for ticket {ticket.ticket_number}")

        except Exception as e:
            logger.error(f"❌ [BG] Scout Support inbound error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())


@email_bp.route('/email-parsing')
@login_required
def email_parsing_dashboard():
    """Dashboard for email parsing monitoring"""
    from models import ParsedEmail
    
    recent_emails = ParsedEmail.query.order_by(
        ParsedEmail.received_at.desc()
    ).limit(100).all()
    
    total_emails = ParsedEmail.query.count()
    completed_emails = ParsedEmail.query.filter_by(status='completed').count()
    failed_emails = ParsedEmail.query.filter_by(status='failed').count()
    duplicate_candidates = ParsedEmail.query.filter_by(is_duplicate_candidate=True).count()
    
    stats = {
        'total': total_emails,
        'completed': completed_emails,
        'failed': failed_emails,
        'duplicates': duplicate_candidates,
        'success_rate': round((completed_emails / total_emails * 100) if total_emails > 0 else 0, 1),
        'duplicate_rate': round((duplicate_candidates / completed_emails * 100) if completed_emails > 0 else 0, 1)
    }
    
    return render_template('email_parsing.html', emails=recent_emails, stats=stats, active_page='email_parsing')


@email_bp.route('/api/email/parsed')
@login_required
def api_parsed_emails():
    """API endpoint for getting parsed emails with pagination"""
    from models import ParsedEmail
    
    page = request.args.get('page', 1, type=int)
    per_page = 50
    status_filter = request.args.get('status')
    source_filter = request.args.get('source')
    
    query = ParsedEmail.query
    
    if status_filter:
        query = query.filter(ParsedEmail.status == status_filter)
    if source_filter:
        query = query.filter(ParsedEmail.source_platform == source_filter)
    
    emails = query.order_by(ParsedEmail.received_at.desc()).paginate(
        page=page, 
        per_page=per_page, 
        error_out=False
    )
    
    return jsonify({
        'emails': [{
            'id': email.id,
            'sender_email': email.sender_email,
            'subject': email.subject[:100] if email.subject else None,
            'source_platform': email.source_platform,
            'bullhorn_job_id': email.bullhorn_job_id,
            'candidate_name': email.candidate_name,
            'candidate_email': email.candidate_email,
            'status': email.status,
            'bullhorn_candidate_id': email.bullhorn_candidate_id,
            'bullhorn_submission_id': email.bullhorn_submission_id,
            'is_duplicate': email.is_duplicate_candidate,
            'duplicate_confidence': email.duplicate_confidence,
            'resume_filename': email.resume_filename,
            'received_at': email.received_at.strftime('%Y-%m-%d %H:%M:%S') if email.received_at else None,
            'processed_at': email.processed_at.strftime('%Y-%m-%d %H:%M:%S') if email.processed_at else None,
            'processing_notes': email.processing_notes
        } for email in emails.items],
        'pagination': {
            'page': emails.page,
            'pages': emails.pages,
            'total': emails.total,
            'has_next': emails.has_next,
            'has_prev': emails.has_prev
        }
    })


@email_bp.route('/api/email/stats')
@login_required
def api_email_parsing_stats():
    """Get email parsing statistics"""
    from models import ParsedEmail
    from sqlalchemy import func
    
    total = ParsedEmail.query.count()
    completed = ParsedEmail.query.filter_by(status='completed').count()
    failed = ParsedEmail.query.filter_by(status='failed').count()
    processing = ParsedEmail.query.filter_by(status='processing').count()
    duplicates = ParsedEmail.query.filter_by(is_duplicate_candidate=True).count()
    
    source_stats = db.session.query(
        ParsedEmail.source_platform,
        func.count(ParsedEmail.id)
    ).group_by(ParsedEmail.source_platform).all()
    
    seven_days_ago = datetime.utcnow() - timedelta(days=7)
    daily_stats = db.session.query(
        func.date(ParsedEmail.received_at),
        func.count(ParsedEmail.id)
    ).filter(
        ParsedEmail.received_at >= seven_days_ago
    ).group_by(
        func.date(ParsedEmail.received_at)
    ).all()
    
    return jsonify({
        'overview': {
            'total': total,
            'completed': completed,
            'failed': failed,
            'processing': processing,
            'duplicates': duplicates,
            'success_rate': round((completed / total * 100) if total > 0 else 0, 1),
            'duplicate_rate': round((duplicates / completed * 100) if completed > 0 else 0, 1)
        },
        'by_source': {source or 'Unknown': count for source, count in source_stats},
        'daily': {str(date): count for date, count in daily_stats}
    })


@email_bp.route('/api/email/clear-stuck', methods=['POST'])
@login_required
def api_clear_stuck_emails():
    """Manually clear stuck email parsing records (mark as failed after timeout)"""
    try:
        from models import ParsedEmail
        
        timeout_threshold = datetime.utcnow() - timedelta(minutes=10)
        
        stuck_records = ParsedEmail.query.filter(
            ParsedEmail.status == 'processing',
            ParsedEmail.created_at < timeout_threshold
        ).all()
        
        if stuck_records:
            cleared_ids = []
            for record in stuck_records:
                record.status = 'failed'
                record.processing_notes = f"Manually cleared: Processing timeout (started at {record.created_at})"
                record.processed_at = datetime.utcnow()
                cleared_ids.append(record.id)
                logger.info(f"⏰ Manually cleared stuck email parsing record ID {record.id} (candidate: {record.candidate_name or 'Unknown'})")
            
            db.session.commit()
            return jsonify({
                'success': True,
                'message': f'Cleared {len(cleared_ids)} stuck records',
                'cleared_ids': cleared_ids
            })
        else:
            return jsonify({
                'success': True,
                'message': 'No stuck records found (records must be processing for >10 minutes)',
                'cleared_ids': []
            })
            
    except Exception as e:
        logger.error(f"Error clearing stuck email records: {str(e)}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@email_bp.route('/api/email/mailbox-backfill', methods=['POST'])
@login_required
def api_mailbox_backfill():
    """One-time, bounded backlog recovery from the apply@ mailbox (Microsoft
    Graph). Processes messages received since `since` (ISO8601 UTC), oldest-first,
    up to `limit`, through the existing inbound pipeline. Safe to re-run —
    Message-ID dedupe prevents double-submission to Bullhorn.

    Admin-guarded (blueprint-level). Accepts JSON or form:
        since: ISO8601 UTC, e.g. '2026-06-04T04:00:00Z' (required)
        limit: int max messages (default 500, capped 2000)
    """
    try:
        data = request.get_json(silent=True) or request.form
        since = (data.get('since') or '').strip()
        if not since:
            return jsonify({
                'success': False,
                'error': "Missing 'since' (ISO8601 UTC, e.g. 2026-06-04T04:00:00Z)"
            }), 400
        try:
            limit = int(data.get('limit', 500))
        except (ValueError, TypeError):
            limit = 500
        limit = max(1, min(limit, 2000))

        from tasks import run_mailbox_backfill
        app_ref = current_app._get_current_object()
        logger.info(f"📥 Mailbox backfill requested: since={since} limit={limit}")
        summary = run_mailbox_backfill(app_ref, since, limit=limit)
        return jsonify({'success': 'error' not in summary, 'summary': summary})
    except Exception as e:
        logger.error(f"Mailbox backfill route error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


_OUTAGE_RECOVERY_PG_LOCK_KEY = 728193641  # stable bigint for pg_advisory_xact_lock
_OUTAGE_RECOVERY_MARKER_KEY = 'outage_recovery_in_progress'  # VettingConfig: locked_at ISO or ''
_OUTAGE_RECOVERY_STALE_MINUTES = 180  # marker older than this = abandoned run -> takeover


def _acquire_recovery_marker():
    """Atomically claim the cross-worker outage-recovery marker.

    The app runs multiple gunicorn workers, so a per-process lock is not enough:
    two workers could otherwise start overlapping backfills and double-submit to
    Bullhorn (the row's UNIQUE message_id only blocks the duplicate ROW, and only
    AFTER the Bullhorn write already happened). A transaction-scoped Postgres
    advisory lock serializes the check-and-set across workers; the marker row
    then holds the 'in progress' state for the whole background run. A stale
    marker (crashed run, older than _OUTAGE_RECOVERY_STALE_MINUTES) is taken
    over. Returns True if claimed, False if a non-stale run already holds it.
    """
    from sqlalchemy import text
    from models import VettingConfig
    now = datetime.utcnow()
    db.session.execute(
        text("SELECT pg_advisory_xact_lock(:k)"),
        {"k": _OUTAGE_RECOVERY_PG_LOCK_KEY})
    marker = (VettingConfig.get_value(_OUTAGE_RECOVERY_MARKER_KEY, '') or '').strip()
    if marker:
        try:
            locked_at = datetime.fromisoformat(marker)
            if (now - locked_at) <= timedelta(minutes=_OUTAGE_RECOVERY_STALE_MINUTES):
                db.session.commit()  # release advisory lock; leave marker intact
                return False
        except ValueError:
            pass  # unparseable marker -> treat as abandoned, take over
    # set_value commits, which persists the marker AND releases the advisory lock.
    VettingConfig.set_value(_OUTAGE_RECOVERY_MARKER_KEY, now.isoformat())
    return True


def _release_recovery_marker(app_ref):
    """Clear the cross-worker recovery marker. Safe from the background thread
    (own app context) or the request finally; tolerates a dirty session."""
    try:
        with app_ref.app_context():
            from models import VettingConfig
            try:
                db.session.rollback()
            except Exception:  # noqa: BLE001
                pass
            VettingConfig.set_value(_OUTAGE_RECOVERY_MARKER_KEY, '')
    except Exception as ex:  # noqa: BLE001
        try:
            app_ref.logger.error(f"Outage-recovery: failed to clear marker: {ex}")
        except Exception:  # noqa: BLE001
            pass


@email_bp.route('/api/email/outage-recovery', methods=['POST'])
@login_required
def api_outage_recovery():
    """One-time recovery for inbound applications stranded by a Bullhorn outage.

    During a Bullhorn auth/API outage the candidate write fails but the row is
    still marked 'completed'/'failed' with a NULL bullhorn_candidate_id, and the
    Message-ID dedupe then blocks any straight retry. This endpoint:

      1. Finds rows received since `since` (the outage start) that never reached
         Bullhorn (bullhorn_candidate_id IS NULL), EXCLUDING rows intentionally
         not submitted (duplicate / ignored) or already superseded.
      2. Clears their message_id and marks them 'recovery_superseded' (kept for
         audit) so the dedupe no longer matches.
      3. Re-drives the outage window through run_mailbox_backfill, which
         re-fetches each email from the apply@ mailbox and re-runs the FULL
         inbound pipeline (candidate create/enrich, notes, résumé upload, job
         submission).

    Double-submission safety has two layers: (a) a PRE-SKIP that leaves a
    stranded row alone when the SAME application (candidate_email + job) already
    produced a Bullhorn submission via a sibling transport copy within the
    cross-route dedupe window of its receive time — this covers partial outages
    the live now-anchored cross-route guard cannot see; and (b) a cross-worker
    single-flight marker so two gunicorn workers cannot run overlapping
    backfills. Operators should eyeball `skipped_sample` (dry run) first, since a
    genuine repeat-apply INSIDE the window is treated as already-submitted (same
    tradeoff as the live cross-route dedupe).

    Safe to re-run: recovered rows get a fresh message_id (dedupe-protected) and
    superseded/skipped rows are excluded by the status filter.

    Admin-guarded (blueprint-level) + login required. Accepts JSON or form:
        since:   ISO8601 UTC outage start (required), e.g. '2026-06-19T02:49:58Z'
        limit:   max messages for the backfill (default 500, cap 2000)
        dry_run: 'true' (DEFAULT) reports what WOULD be reset without changing
                 anything; pass 'false' to perform the reset + backfill.
    """
    from models import ParsedEmail
    try:
        data = request.get_json(silent=True) or request.form
        since = (data.get('since') or '').strip()
        if not since:
            return jsonify({
                'success': False,
                'error': "Missing 'since' (ISO8601 UTC, e.g. 2026-06-19T02:49:58Z)"
            }), 400

        # Parse `since` to a naive-UTC datetime to match received_at, which is
        # stored as naive UTC (datetime.utcnow()).
        try:
            since_dt = datetime.fromisoformat(since.replace('Z', '+00:00'))
            if since_dt.tzinfo is not None:
                since_dt = since_dt.astimezone(timezone.utc).replace(tzinfo=None)
        except ValueError:
            return jsonify({
                'success': False,
                'error': f"Bad 'since' format (expected ISO8601 UTC): {since}"
            }), 400

        try:
            limit = int(data.get('limit', 500))
        except (ValueError, TypeError):
            limit = 500
        limit = max(1, min(limit, 2000))

        dry_run = str(data.get('dry_run', 'true')).strip().lower() != 'false'

        # Read the cross-route dedupe window (minutes) so recovery uses the SAME
        # notion of "same application via a different transport" as the live
        # pipeline. 0 disables the guard; capped at 24h. Default 30.
        try:
            from models import VettingConfig
            window_min = int(VettingConfig.get_value(
                'cross_route_dedupe_window_minutes', '30'))
        except (TypeError, ValueError):
            window_min = 30
        except Exception:  # noqa: BLE001
            window_min = 30
        window_min = max(0, min(window_min, 1440))

        # Stranded = received in the outage window, never written to Bullhorn,
        # and not intentionally skipped (duplicate/ignored) or already handled by
        # a prior recovery. message_id IS NOT NULL so there is a dedupe key to
        # clear; the backfill re-fetches each email from the mailbox.
        stranded = ParsedEmail.query.filter(
            ParsedEmail.received_at >= since_dt,
            ParsedEmail.bullhorn_candidate_id.is_(None),
            ParsedEmail.message_id.isnot(None),
            ~ParsedEmail.status.in_(
                ['duplicate', 'ignored', 'recovery_superseded',
                 'recovery_skipped_submitted']),
        ).order_by(ParsedEmail.received_at.asc()).all()
        stranded_count = len(stranded)

        # Split stranded rows. A row whose SAME application (candidate_email +
        # bullhorn_job_id) already reached Bullhorn via a sibling transport copy
        # WITHIN the dedupe window of its own receive time must NOT be re-driven —
        # doing so would create a duplicate JobSubmission (the live cross-route
        # guard only looks back from "now", so it cannot see an hours-old sibling
        # left over from a partial outage). Genuine repeat-applies (days apart)
        # fall OUTSIDE the window and are still re-driven correctly.
        to_redrive = []
        skipped_submitted = []
        for pe in stranded:
            sibling = None
            if (window_min > 0 and pe.candidate_email
                    and pe.bullhorn_job_id and pe.received_at):
                lo = pe.received_at - timedelta(minutes=window_min)
                hi = pe.received_at + timedelta(minutes=window_min)
                sibling = ParsedEmail.query.filter(
                    ParsedEmail.id != pe.id,
                    ParsedEmail.candidate_email == pe.candidate_email,
                    ParsedEmail.bullhorn_job_id == pe.bullhorn_job_id,
                    ParsedEmail.bullhorn_submission_id.isnot(None),
                    ParsedEmail.received_at >= lo,
                    ParsedEmail.received_at <= hi,
                ).first()
            if sibling is not None:
                skipped_submitted.append((pe, sibling))
            else:
                to_redrive.append(pe)

        if dry_run:
            return jsonify({
                'success': True,
                'dry_run': True,
                'since': since,
                'window_minutes': window_min,
                'stranded_count': stranded_count,
                'redrive_count': len(to_redrive),
                'skipped_already_submitted_count': len(skipped_submitted),
                'sample': [
                    {
                        'id': pe.id,
                        'received_at': pe.received_at.isoformat() if pe.received_at else None,
                        'status': pe.status,
                        'candidate_email': pe.candidate_email,
                        'bullhorn_job_id': pe.bullhorn_job_id,
                    }
                    for pe in to_redrive[:10]
                ],
                'skipped_sample': [
                    {
                        'id': pe.id,
                        'candidate_email': pe.candidate_email,
                        'bullhorn_job_id': pe.bullhorn_job_id,
                        'already_submitted_via_id': sib.id,
                    }
                    for pe, sib in skipped_submitted[:10]
                ],
                'message': (
                    f"DRY RUN — {len(to_redrive)} stranded rows would be reset and "
                    f"re-driven; {len(skipped_submitted)} skipped (already reached "
                    f"Bullhorn via another transport within {window_min}m). "
                    f"No changes made. Re-send with dry_run=false to execute."
                ),
            })

        # --- live run: cross-worker single-flight via a DB marker (the app runs
        # multiple gunicorn workers, so an in-process lock cannot prevent two
        # workers from launching overlapping backfills). Claimed atomically and
        # released when the backfill finishes; a stale marker is taken over. ---
        if not _acquire_recovery_marker():
            return jsonify({
                'success': False,
                'error': ('An outage-recovery run is already in progress; '
                          'wait for it to finish before starting another.'),
            }), 409
        ownership_transferred = False
        try:
            stamp = datetime.utcnow().isoformat()
            reset_ids = []
            for pe in to_redrive:
                reset_ids.append(pe.id)
                pe.message_id = None
                pe.status = 'recovery_superseded'
                pe.processing_notes = (
                    (pe.processing_notes or '')
                    + f" | Outage-recovery {stamp}Z: message_id cleared; "
                      f"superseded by mailbox-backfill re-drive."
                )[:2000]

            skipped_ids = []
            for pe, sib in skipped_submitted:
                skipped_ids.append(pe.id)
                pe.status = 'recovery_skipped_submitted'
                pe.processing_notes = (
                    (pe.processing_notes or '')
                    + f" | Outage-recovery {stamp}Z: NOT re-driven — same "
                      f"application already submitted via ParsedEmail {sib.id} "
                      f"(submission {sib.bullhorn_submission_id}) within "
                      f"{window_min}m."
                )[:2000]
            db.session.commit()
            logger.info(
                f"📥 Outage-recovery: reset {len(reset_ids)} stranded rows, "
                f"skipped {len(skipped_ids)} already-submitted (since {since}); "
                f"starting background backfill (limit={limit})"
            )

            # Re-drive in a background thread — dozens of emails × AI parse +
            # Bullhorn writes can exceed the request timeout. The thread owns the
            # cross-worker marker and clears it when the backfill finishes.
            app_ref = current_app._get_current_object()

            def _bg_backfill():
                try:
                    from tasks import run_mailbox_backfill
                    summary = run_mailbox_backfill(app_ref, since, limit=limit)
                    app_ref.logger.info(
                        f"📥 Outage-recovery backfill complete: {summary}")
                finally:
                    _release_recovery_marker(app_ref)

            threading.Thread(target=_bg_backfill, daemon=True).start()
            ownership_transferred = True

            return jsonify({
                'success': True,
                'dry_run': False,
                'since': since,
                'window_minutes': window_min,
                'reset_count': len(reset_ids),
                'reset_ids': reset_ids,
                'skipped_already_submitted_count': len(skipped_ids),
                'skipped_ids': skipped_ids,
                'message': (
                    f"Reset {len(reset_ids)} stranded rows ({len(skipped_ids)} "
                    f"skipped as already-submitted); mailbox-backfill re-drive "
                    f"started in the background. Monitor parsed_email for "
                    f"bullhorn_candidate_id filling in."
                ),
            })
        finally:
            if not ownership_transferred:
                _release_recovery_marker(current_app._get_current_object())
    except Exception as e:
        db.session.rollback()
        logger.error(f"Outage-recovery route error: {e}")
        import traceback
        logger.error(traceback.format_exc())
        return jsonify({'success': False, 'error': str(e)}), 500


@email_bp.route('/api/email/test-parse', methods=['POST'])
@login_required 
def api_test_email_parse():
    """Test endpoint to simulate email parsing (for development)"""
    try:
        from email_inbound_service import EmailInboundService
        
        data = request.get_json()
        if not data:
            return jsonify({'error': 'No data provided'}), 400
        
        service = EmailInboundService()
        
        source = service.detect_source(
            data.get('from', ''),
            data.get('subject', ''),
            data.get('body', '')
        )
        
        job_id = service.extract_bullhorn_job_id(
            data.get('subject', ''),
            data.get('body', '')
        )
        
        candidate = service.extract_candidate_from_email(
            data.get('subject', ''),
            data.get('body', ''),
            source
        )
        
        return jsonify({
            'source_detected': source,
            'job_id': job_id,
            'candidate': candidate
        })
        
    except Exception as e:
        return jsonify({'error': str(e)}), 500
