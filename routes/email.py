import json
import logging
import re
import threading
from datetime import datetime, timedelta
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
        
        payload = request.form.to_dict()
        
        if request.files:
            for key, file in request.files.items():
                payload[key] = file.read()
                payload[f'{key}_info'] = {
                    'filename': file.filename,
                    'content_type': file.content_type
                }
        
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
